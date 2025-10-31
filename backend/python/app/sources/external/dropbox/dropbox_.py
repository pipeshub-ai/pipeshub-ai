import asyncio
from typing import Dict, List, Optional, Union

import dropbox
from dropbox import Dropbox, DropboxTeam
from dropbox.file_properties import TemplateFilter  # type: ignore
from dropbox.file_requests import UpdateFileRequestDeadline  # type: ignore
from dropbox.files import (  # type: ignore
    ListRevisionsMode,
    SearchMode,
    ThumbnailFormat,
    ThumbnailMode,
    ThumbnailSize,
    WriteMode,
)
from dropbox.paper import (  # type: ignore
    ListPaperDocsFilterBy,
    ListPaperDocsSortBy,
    ListPaperDocsSortOrder,
    UserOnPaperDocFilter,
)
from dropbox.sharing import AccessInheritance, AccessLevel  # type: ignore
from dropbox.team import GroupSelector, UserSelectorArg

from app.sources.client.dropbox.dropbox_ import DropboxClient, DropboxResponse


class DropboxDataSource:
    """
    Complete Dropbox API client wrapper using official SDK
    Auto-generated wrapper for Dropbox SDK methods.
    This class provides unified access to all Dropbox SDK methods while
    maintaining the official SDK structure and behavior.
    Coverage:
    - Total SDK methods: 268
    - Auto-discovered from official Dropbox Python SDK
    """

    def __init__(self, dropboxClient: DropboxClient) -> None:
        """
        Initialize the Dropbox SDK wrapper.
        Args:
            dropboxClient (DropboxClient): Dropbox client instance
        """
        self._dropbox_client = dropboxClient
        self._base_sdk_client: Union[Dropbox, DropboxTeam] = self._dropbox_client.get_client().create_client()
        # self._user_client = None
        # self._team_client = None

    async def _get_user_client(self, team_member_id: Optional[str] = None, as_admin: bool = False) -> Union[Dropbox, DropboxTeam]:
        """
        Gets a Dropbox client scoped to a specific user or admin.

        --- FIX: Use the cached base client ---
        """
        # Get the cached base client created in __init__
        base_client = self._base_sdk_client

        # If this is a team client and a member ID is provided, scope it
        if isinstance(base_client, DropboxTeam) and team_member_id:
            if as_admin:
                # Return a client scoped as an Admin
                return base_client.as_admin(team_member_id)
            else:
                # Return a client scoped as a User
                return base_client.as_user(team_member_id)

        # Otherwise, return the base client (either non-team or team-level)
        return base_client


    async def _get_team_client(self) -> DropboxTeam:
        """
        Get or create team client.

        --- FIX: Use the cached base client ---
        """
        # Return the cached client, ensuring it's the correct type
        if isinstance(self._base_sdk_client, DropboxTeam):
            return self._base_sdk_client

        # This will now fail fast if it was initialized as a non-team client
        raise Exception("Team operations require a team client, but a non-team client was initialized.")

    async def account_set_profile_photo(
        self,
        photo: str
    ) -> DropboxResponse:
        """Sets a user's profile photo.

        API Endpoint: /2/users/set_profile_photo
        Namespace: account
        Client type: user

        Args:
            photo (str, required): Parameter for account_set_profile_photo

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: account_info.write
            :param photo: Image to set as the user's new profile photo.
            :type photo: :class:`dropbox.account.PhotoSourceArg`
            :rtype: :class:`dropbox.account.SetProfilePhotoResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.account.SetProfilePhotoError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.account_set_profile_photo(photo))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def auth_token_from_oauth1(
        self,
        oauth1_token: str,
        oauth1_token_secret: str
    ) -> DropboxResponse:
        """Creates an OAuth 2.0 access token from the supplied OAuth 1.0 access

        API Endpoint: /2/auth/token_from_oauth1
        Namespace: auth
        Client type: user

        Args:
            oauth1_token (str, required): Parameter for auth_token_from_oauth1
            oauth1_token_secret (str, required): Parameter for auth_token_from_oauth1

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            token.
            :param str oauth1_token: The supplied OAuth 1.0 access token.
            :param str oauth1_token_secret: The token secret associated with the
            supplied access token.
            :rtype: :class:`dropbox.auth.TokenFromOAuth1Result`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.auth.TokenFromOAuth1Error`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.auth_token_from_oauth1(oauth1_token, oauth1_token_secret))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def auth_token_revoke(self) -> DropboxResponse:
        """Disables the access token used to authenticate the call. If there is a

        API Endpoint: /2/auth/token_revoke
        Namespace: auth
        Client type: user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            corresponding refresh token for the access token, this disables that
            refresh token, as well as any other access tokens for that refresh
            token.
            :rtype: None
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.auth_token_revoke())
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def check_app(
        self,
        query: str = ""
    ) -> DropboxResponse:
        """This endpoint performs App Authentication, validating the supplied app

        API Endpoint: /2/check/app
        Namespace: check
        Client type: user

        Args:
            query (str, optional): Parameter for check_app

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            key and secret, and returns the supplied string, to allow you to test
            your code and connection to the Dropbox API. It has no other effect. If
            you receive an HTTP 200 response with the supplied query, it indicates
            at least part of the Dropbox API infrastructure is working and that the
            app key and secret valid.
            :param str query: The string that you'd like to be echoed back to you.
            :rtype: :class:`dropbox.check.EchoResult`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.check_app(query=query))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def check_user(
        self,
        query: str = ""
    ) -> DropboxResponse:
        """This endpoint performs User Authentication, validating the supplied

        API Endpoint: /2/check/user
        Namespace: check
        Client type: user

        Args:
            query (str, optional): Parameter for check_user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            access token, and returns the supplied string, to allow you to test your
            code and connection to the Dropbox API. It has no other effect. If you
            receive an HTTP 200 response with the supplied query, it indicates at
            least part of the Dropbox API infrastructure is working and that the
            access token is valid.
            Route attributes:
            scope: account_info.read
            :param str query: The string that you'd like to be echoed back to you.
            :rtype: :class:`dropbox.check.EchoResult`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.check_user(query=query))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def contacts_delete_manual_contacts(self) -> DropboxResponse:
        """Removes all manually added contacts. You'll still keep contacts who are

        API Endpoint: /2/contacts/delete_manual_contacts
        Namespace: contacts
        Client type: user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            on your team or who you imported. New contacts will be added when you
            share.
            Route attributes:
            scope: contacts.write
            :rtype: None
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.contacts_delete_manual_contacts())
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def contacts_delete_manual_contacts_batch(
        self,
        email_addresses: str
    ) -> DropboxResponse:
        """Removes manually added contacts from the given list.

        API Endpoint: /2/contacts/delete_manual_contacts_batch
        Namespace: contacts
        Client type: user

        Args:
            email_addresses (str, required): Parameter for contacts_delete_manual_contacts_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: contacts.write
            :param List[str] email_addresses: List of manually added contacts to be
            deleted.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.contacts.DeleteManualContactsError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.contacts_delete_manual_contacts_batch(email_addresses))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_properties_properties_add(
        self,
        path: str,
        property_groups: str
    ) -> DropboxResponse:
        """Add property groups to a Dropbox file. See

        API Endpoint: /2/file/properties_properties_add
        Namespace: file
        Client type: user

        Args:
            path (str, required): Parameter for file_properties_properties_add
            property_groups (str, required): Parameter for file_properties_properties_add

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`file_properties_templates_add_for_user` or
            :meth:`file_properties_templates_add_for_team` to create new templates.
            Route attributes:
            scope: files.metadata.write
            :param str path: A unique identifier for the file or folder.
            :param List[:class:`dropbox.file_properties.PropertyGroup`]
            property_groups: The property groups which are to be added to a
            Dropbox file. No two groups in the input should  refer to the same
            template.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.AddPropertiesError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.file_properties_properties_add(path, property_groups))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_properties_properties_overwrite(
        self,
        path: str,
        property_groups: str
    ) -> DropboxResponse:
        """Overwrite property groups associated with a file. This endpoint should

        API Endpoint: /2/file/properties_properties_overwrite
        Namespace: file
        Client type: user

        Args:
            path (str, required): Parameter for file_properties_properties_overwrite
            property_groups (str, required): Parameter for file_properties_properties_overwrite

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            be used instead of :meth:`file_properties_properties_update` when
            property groups are being updated via a "snapshot" instead of via a
            "delta". In other words, this endpoint will delete all omitted fields
            from a property group, whereas :meth:`file_properties_properties_update`
            will only delete fields that are explicitly marked for deletion.
            Route attributes:
            scope: files.metadata.write
            :param str path: A unique identifier for the file or folder.
            :param List[:class:`dropbox.file_properties.PropertyGroup`]
            property_groups: The property groups "snapshot" updates to force
            apply. No two groups in the input should  refer to the same
            template.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.InvalidPropertyGroupError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.file_properties_properties_overwrite(path, property_groups))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_properties_properties_remove(
        self,
        path: str,
        property_template_ids: str
    ) -> DropboxResponse:
        """Permanently removes the specified property group from the file. To

        API Endpoint: /2/file/properties_properties_remove
        Namespace: file
        Client type: user

        Args:
            path (str, required): Parameter for file_properties_properties_remove
            property_template_ids (str, required): Parameter for file_properties_properties_remove

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            remove specific property field key value pairs, see
            :meth:`file_properties_properties_update`. To update a template, see
            :meth:`file_properties_templates_update_for_user` or
            :meth:`file_properties_templates_update_for_team`. To remove a template,
            see :meth:`file_properties_templates_remove_for_user` or
            :meth:`file_properties_templates_remove_for_team`.
            Route attributes:
            scope: files.metadata.write
            :param str path: A unique identifier for the file or folder.
            :param List[str] property_template_ids: A list of identifiers for a
            template created by :meth:`file_properties_templates_add_for_user`
            or :meth:`file_properties_templates_add_for_team`.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.RemovePropertiesError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.file_properties_properties_remove(path, property_template_ids))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_properties_properties_search(
        self,
        queries: str,
        template_filter: str = TemplateFilter('filter_none', None)
    ) -> DropboxResponse:
        """Search across property templates for particular property field values.

        API Endpoint: /2/file/properties_properties_search
        Namespace: file
        Client type: user

        Args:
            queries (str, required): Parameter for file_properties_properties_search
            template_filter (str, optional): Parameter for file_properties_properties_search

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.metadata.read
            :param List[:class:`dropbox.file_properties.PropertiesSearchQuery`]
            queries: Queries to search.
            :param template_filter: Filter results to contain only properties
            associated with these template IDs.
            :type template_filter: :class:`dropbox.file_properties.TemplateFilter`
            :rtype: :class:`dropbox.file_properties.PropertiesSearchResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.PropertiesSearchError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.file_properties_properties_search(queries, template_filter=template_filter))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_properties_properties_search_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from

        API Endpoint: /2/file/properties_properties_search_continue
        Namespace: file
        Client type: user

        Args:
            cursor (str, required): Parameter for file_properties_properties_search_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`file_properties_properties_search`, use this to paginate through
            all search results.
            Route attributes:
            scope: files.metadata.read
            :param str cursor: The cursor returned by your last call to
            :meth:`file_properties_properties_search` or
            :meth:`file_properties_properties_search_continue`.
            :rtype: :class:`dropbox.file_properties.PropertiesSearchResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.PropertiesSearchContinueError`
        """
        client = await self._get_user_client()
        try:
            response = client.file_properties_properties_search_continue(cursor)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_properties_properties_update(
        self,
        path: str,
        update_property_groups: str
    ) -> DropboxResponse:
        """Add, update or remove properties associated with the supplied file and

        API Endpoint: /2/file/properties_properties_update
        Namespace: file
        Client type: user

        Args:
            path (str, required): Parameter for file_properties_properties_update
            update_property_groups (str, required): Parameter for file_properties_properties_update

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            templates. This endpoint should be used instead of
            :meth:`file_properties_properties_overwrite` when property groups are
            being updated via a "delta" instead of via a "snapshot" . In other
            words, this endpoint will not delete any omitted fields from a property
            group, whereas :meth:`file_properties_properties_overwrite` will delete
            any fields that are omitted from a property group.
            Route attributes:
            scope: files.metadata.write
            :param str path: A unique identifier for the file or folder.
            :param List[:class:`dropbox.file_properties.PropertyGroupUpdate`]
            update_property_groups: The property groups "delta" updates to
            apply.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.UpdatePropertiesError`
        """
        client = await self._get_user_client()
        try:
            response = client.file_properties_properties_update(path, update_property_groups)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_properties_templates_add_for_user(
        self,
        name: str,
        description: str,
        fields: str
    ) -> DropboxResponse:
        """Add a template associated with a user. See

        API Endpoint: /2/file/properties_templates_add_for_user
        Namespace: file
        Client type: user

        Args:
            name (str, required): Parameter for file_properties_templates_add_for_user
            description (str, required): Parameter for file_properties_templates_add_for_user
            fields (str, required): Parameter for file_properties_templates_add_for_user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`file_properties_properties_add` to add properties to a file. This
            endpoint can't be called on a team member or admin's behalf.
            Route attributes:
            scope: files.metadata.write
            :rtype: :class:`dropbox.file_properties.AddTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.ModifyTemplateError`
        """
        client = await self._get_user_client()
        try:
            response = client.file_properties_templates_add_for_user(name, description, fields)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_properties_templates_get_for_user(
        self,
        template_id: str
    ) -> DropboxResponse:
        """Get the schema for a specified template. This endpoint can't be called

        API Endpoint: /2/file/properties_templates_get_for_user
        Namespace: file
        Client type: user

        Args:
            template_id (str, required): Parameter for file_properties_templates_get_for_user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            on a team member or admin's behalf.
            Route attributes:
            scope: files.metadata.read
            :param str template_id: An identifier for template added by route  See
            :meth:`file_properties_templates_add_for_user` or
            :meth:`file_properties_templates_add_for_team`.
            :rtype: :class:`dropbox.file_properties.GetTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.TemplateError`
        """
        client = await self._get_user_client()
        try:
            response = client.file_properties_templates_get_for_user(template_id)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_properties_templates_list_for_user(self) -> DropboxResponse:
        """Get the template identifiers for a team. To get the schema of each

        API Endpoint: /2/file/properties_templates_list_for_user
        Namespace: file
        Client type: user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            template use :meth:`file_properties_templates_get_for_user`. This
            endpoint can't be called on a team member or admin's behalf.
            Route attributes:
            scope: files.metadata.read
            :rtype: :class:`dropbox.file_properties.ListTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.TemplateError`
        """
        client = await self._get_user_client()
        try:
            response = client.file_properties_templates_list_for_user()
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_properties_templates_remove_for_user(
        self,
        template_id: str
    ) -> DropboxResponse:
        """Permanently removes the specified template created from

        API Endpoint: /2/file/properties_templates_remove_for_user
        Namespace: file
        Client type: user

        Args:
            template_id (str, required): Parameter for file_properties_templates_remove_for_user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`file_properties_templates_add_for_user`. All properties
            associated with the template will also be removed. This action cannot be
            undone.
            Route attributes:
            scope: files.metadata.write
            :param str template_id: An identifier for a template created by
            :meth:`file_properties_templates_add_for_user` or
            :meth:`file_properties_templates_add_for_team`.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.TemplateError`
        """
        client = await self._get_user_client()
        try:
            response = client.file_properties_templates_remove_for_user(template_id)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_properties_templates_update_for_user(
        self,
        template_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        add_fields: Optional[str] = None
    ) -> DropboxResponse:
        """Update a template associated with a user. This route can update the

        API Endpoint: /2/file/properties_templates_update_for_user
        Namespace: file
        Client type: user

        Args:
            template_id (str, required): Parameter for file_properties_templates_update_for_user
            name (str, optional): Parameter for file_properties_templates_update_for_user
            description (str, optional): Parameter for file_properties_templates_update_for_user
            add_fields (str, optional): Parameter for file_properties_templates_update_for_user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            template name, the template description and add optional properties to
            templates. This endpoint can't be called on a team member or admin's
            behalf.
            Route attributes:
            scope: files.metadata.write
            :param str template_id: An identifier for template added by  See
            :meth:`file_properties_templates_add_for_user` or
            :meth:`file_properties_templates_add_for_team`.
            :param Nullable[str] name: A display name for the template. template
            names can be up to 256 bytes.
            :param Nullable[str] description: Description for the new template.
            Template descriptions can be up to 1024 bytes.
            :param
            Nullable[List[:class:`dropbox.file_properties.PropertyFieldTemplate`]]
            add_fields: Property field templates to be added to the group
            template. There can be up to 32 properties in a single template.
            :rtype: :class:`dropbox.file_properties.UpdateTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.ModifyTemplateError`
        """
        client = await self._get_user_client()
        try:
            response = client.file_properties_templates_update_for_user(template_id, name=name, description=description, add_fields=add_fields)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_requests_count(self) -> DropboxResponse:
        """Returns the total number of file requests owned by this user. Includes

        API Endpoint: /2/file/requests_count
        Namespace: file
        Client type: user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            both open and closed file requests.
            Route attributes:
            scope: file_requests.read
            :rtype: :class:`dropbox.file_requests.CountFileRequestsResult`
        """
        client = await self._get_user_client()
        try:
            response = client.file_requests_count()
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_requests_create(
        self,
        title: str,
        destination: str,
        deadline: Optional[str] = None,
        open: str = True,
        description: Optional[str] = None
    ) -> DropboxResponse:
        """Creates a file request for this user.

        API Endpoint: /2/file/requests_create
        Namespace: file
        Client type: user

        Args:
            title (str, required): Parameter for file_requests_create
            destination (str, required): Parameter for file_requests_create
            deadline (str, optional): Parameter for file_requests_create
            open (str, optional): Parameter for file_requests_create
            description (str, optional): Parameter for file_requests_create

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: file_requests.write
            :param str title: The title of the file request. Must not be empty.
            :param str destination: The path of the folder in the Dropbox where
            uploaded files will be sent. For apps with the app folder
            permission, this will be relative to the app folder.
            :param Nullable[:class:`dropbox.file_requests.FileRequestDeadline`]
            deadline: The deadline for the file request. Deadlines can only be
            set by Professional and Business accounts.
            :param bool open: Whether or not the file request should be open. If the
            file request is closed, it will not accept any file submissions, but
            it can be opened later.
            :param Nullable[str] description: A description of the file request.
            :rtype: :class:`dropbox.file_requests.FileRequest`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_requests.CreateFileRequestError`
        """
        client = await self._get_user_client()
        try:
            response = client.file_requests_create(title, destination, deadline=deadline, open=open, description=description)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_requests_delete(
        self,
        ids: str
    ) -> DropboxResponse:
        """Delete a batch of closed file requests.

        API Endpoint: /2/file/requests_delete
        Namespace: file
        Client type: user

        Args:
            ids (str, required): Parameter for file_requests_delete

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: file_requests.write
            :param List[str] ids: List IDs of the file requests to delete.
            :rtype: :class:`dropbox.file_requests.DeleteFileRequestsResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_requests.DeleteFileRequestError`
        """
        client = await self._get_user_client()
        try:
            response = client.file_requests_delete(ids)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_requests_delete_all_closed(self) -> DropboxResponse:
        """Delete all closed file requests owned by this user.

        API Endpoint: /2/file/requests_delete_all_closed
        Namespace: file
        Client type: user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: file_requests.write
            :rtype: :class:`dropbox.file_requests.DeleteAllClosedFileRequestsResult`
        """
        client = await self._get_user_client()
        try:
            response = client.file_requests_delete_all_closed()
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_requests_get(
        self,
        id: str
    ) -> DropboxResponse:
        """Returns the specified file request.

        API Endpoint: /2/file/requests_get
        Namespace: file
        Client type: user

        Args:
            id (str, required): Parameter for file_requests_get

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: file_requests.read
            :param str id: The ID of the file request to retrieve.
            :rtype: :class:`dropbox.file_requests.FileRequest`
        """
        client = await self._get_user_client()
        try:
            response = client.file_requests_get(id)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_requests_list(self) -> DropboxResponse:
        """Returns a list of file requests owned by this user. For apps with the

        API Endpoint: /2/file/requests_list
        Namespace: file
        Client type: user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            app folder permission, this will only return file requests with
            destinations in the app folder.
            Route attributes:
            scope: file_requests.read
            :rtype: :class:`dropbox.file_requests.ListFileRequestsResult`
        """
        client = await self._get_user_client()
        try:
            response = client.file_requests_list()
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_requests_list_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from :meth:`file_requests_list_v2`, use

        API Endpoint: /2/file/requests_list_continue
        Namespace: file
        Client type: user

        Args:
            cursor (str, required): Parameter for file_requests_list_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to paginate through all file requests. The cursor must come from a
            previous call to :meth:`file_requests_list_v2` or
            :meth:`file_requests_list_continue`.
            Route attributes:
            scope: file_requests.read
            :param str cursor: The cursor returned by the previous API call
            specified in the endpoint description.
            :rtype: :class:`dropbox.file_requests.ListFileRequestsV2Result`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_requests.ListFileRequestsContinueError`
        """
        client = await self._get_user_client()
        try:
            response = client.file_requests_list_continue(cursor)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_requests_list_v2(
        self,
        limit: str = 1000
    ) -> DropboxResponse:
        """Returns a list of file requests owned by this user. For apps with the

        API Endpoint: /2/file/requests_list_v2
        Namespace: file
        Client type: user

        Args:
            limit (str, optional): Parameter for file_requests_list_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            app folder permission, this will only return file requests with
            destinations in the app folder.
            Route attributes:
            scope: file_requests.read
            :param int limit: The maximum number of file requests that should be
            returned per request.
            :rtype: :class:`dropbox.file_requests.ListFileRequestsV2Result`
        """
        client = await self._get_user_client()
        try:
            response = client.file_requests_list_v2(limit=limit)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def file_requests_update(
        self,
        id: str,
        title: Optional[str] = None,
        destination: Optional[str] = None,
        deadline: str = UpdateFileRequestDeadline('no_update', None),
        open: Optional[str] = None,
        description: Optional[str] = None
    ) -> DropboxResponse:
        """Update a file request.

        API Endpoint: /2/file/requests_update
        Namespace: file
        Client type: user

        Args:
            id (str, required): Parameter for file_requests_update
            title (str, optional): Parameter for file_requests_update
            destination (str, optional): Parameter for file_requests_update
            deadline (str, optional): Parameter for file_requests_update
            open (str, optional): Parameter for file_requests_update
            description (str, optional): Parameter for file_requests_update

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: file_requests.write
            :param str id: The ID of the file request to update.
            :param Nullable[str] title: The new title of the file request. Must not
            be empty.
            :param Nullable[str] destination: The new path of the folder in the
            Dropbox where uploaded files will be sent. For apps with the app
            folder permission, this will be relative to the app folder.
            :param deadline: The new deadline for the file request. Deadlines can
            only be set by Professional and Business accounts.
            :type deadline: :class:`dropbox.file_requests.UpdateFileRequestDeadline`
            :param Nullable[bool] open: Whether to set this file request as open or
            closed.
            :param Nullable[str] description: The description of the file request.
            :rtype: :class:`dropbox.file_requests.FileRequest`
        """
        client = await self._get_user_client()
        try:
            response = client.file_requests_update(id, title=title, destination=destination, deadline=deadline, open=open, description=description)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_alpha_get_metadata(
        self,
        path: str,
        include_media_info: str = False,
        include_deleted: str = False,
        include_has_explicit_shared_members: str = False,
        include_property_groups: Optional[str] = None,
        include_property_templates: Optional[str] = None
    ) -> DropboxResponse:
        """Returns the metadata for a file or folder. This is an alpha endpoint

        API Endpoint: /2/files/alpha_get_metadata
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_alpha_get_metadata
            include_media_info (str, optional): Parameter for files_alpha_get_metadata
            include_deleted (str, optional): Parameter for files_alpha_get_metadata
            include_has_explicit_shared_members (str, optional): Parameter for files_alpha_get_metadata
            include_property_groups (str, optional): Parameter for files_alpha_get_metadata
            include_property_templates (str, optional): Parameter for files_alpha_get_metadata

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            compatible with the properties API. Note: Metadata for the root folder
            is unsupported.
            Route attributes:
            scope: files.metadata.read
            :param Nullable[List[str]] include_property_templates: If set to a valid
            list of template IDs, ``FileMetadata.property_groups`` is set for
            files with custom properties.
            :rtype: :class:`dropbox.files.Metadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.AlphaGetMetadataError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_alpha_get_metadata(path, include_media_info=include_media_info, include_deleted=include_deleted, include_has_explicit_shared_members=include_has_explicit_shared_members, include_property_groups=include_property_groups, include_property_templates=include_property_templates)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_alpha_upload(
        self,
        f: str,
        path: str,
        mode: str = WriteMode('add', None),
        autorename: str = False,
        client_modified: Optional[str] = None,
        mute: str = False,
        property_groups: Optional[str] = None,
        strict_conflict: str = False,
        content_hash: Optional[str] = None
    ) -> DropboxResponse:
        """Create a new file with the contents provided in the request. Note that

        API Endpoint: /2/files/alpha_upload
        Namespace: files
        Client type: user

        Args:
            f (str, required): Parameter for files_alpha_upload
            path (str, required): Parameter for files_alpha_upload
            mode (str, optional): Parameter for files_alpha_upload
            autorename (str, optional): Parameter for files_alpha_upload
            client_modified (str, optional): Parameter for files_alpha_upload
            mute (str, optional): Parameter for files_alpha_upload
            property_groups (str, optional): Parameter for files_alpha_upload
            strict_conflict (str, optional): Parameter for files_alpha_upload
            content_hash (str, optional): Parameter for files_alpha_upload

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            the behavior of this alpha endpoint is unstable and subject to change.
            Do not use this to upload a file larger than 150 MB. Instead, create an
            upload session with :meth:`files_upload_session_start`.
            Route attributes:
            scope: files.content.write
            :param bytes f: Contents to upload.
            :param Nullable[str] content_hash: A hash of the file content uploaded
            in this call. If provided and the uploaded content does not match
            this hash, an error will be returned. For more information see our
            `Content hash
            <https://www.dropbox.com/developers/reference/content-hash>`_ page.
            :rtype: :class:`dropbox.files.FileMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.UploadError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_alpha_upload(f, path, mode=mode, autorename=autorename, client_modified=client_modified, mute=mute, property_groups=property_groups, strict_conflict=strict_conflict, content_hash=content_hash)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_copy(
        self,
        from_path: str,
        to_path: str,
        allow_shared_folder: str = False,
        autorename: str = False,
        allow_ownership_transfer: str = False
    ) -> DropboxResponse:
        """Copy a file or folder to a different location in the user's Dropbox. If

        API Endpoint: /2/files/copy
        Namespace: files
        Client type: user

        Args:
            from_path (str, required): Parameter for files_copy
            to_path (str, required): Parameter for files_copy
            allow_shared_folder (str, optional): Parameter for files_copy
            autorename (str, optional): Parameter for files_copy
            allow_ownership_transfer (str, optional): Parameter for files_copy

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            the source path is a folder all its contents will be copied.
            Route attributes:
            scope: files.content.write
            :param bool allow_shared_folder: This flag has no effect.
            :param bool autorename: If there's a conflict, have the Dropbox server
            try to autorename the file to avoid the conflict.
            :param bool allow_ownership_transfer: Allow moves by owner even if it
            would result in an ownership transfer for the content being moved.
            This does not apply to copies.
            :rtype: :class:`dropbox.files.Metadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.RelocationError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_copy(from_path, to_path, allow_shared_folder=allow_shared_folder, autorename=autorename, allow_ownership_transfer=allow_ownership_transfer)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_copy_batch(
        self,
        entries: str,
        autorename: str = False,
        allow_shared_folder: str = False,
        allow_ownership_transfer: str = False
    ) -> DropboxResponse:
        """Copy multiple files or folders to different locations at once in the

        API Endpoint: /2/files/copy_batch
        Namespace: files
        Client type: user

        Args:
            entries (str, required): Parameter for files_copy_batch
            autorename (str, optional): Parameter for files_copy_batch
            allow_shared_folder (str, optional): Parameter for files_copy_batch
            allow_ownership_transfer (str, optional): Parameter for files_copy_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            user's Dropbox. This route will return job ID immediately and do the
            async copy job in background. Please use :meth:`files_copy_batch_check`
            to check the job status.
            Route attributes:
            scope: files.content.write
            :param bool allow_shared_folder: This flag has no effect.
            :param bool allow_ownership_transfer: Allow moves by owner even if it
            would result in an ownership transfer for the content being moved.
            This does not apply to copies.
            :rtype: :class:`dropbox.files.RelocationBatchLaunch`
        """
        client = await self._get_user_client()
        try:
            response = client.files_copy_batch(entries, autorename=autorename, allow_shared_folder=allow_shared_folder, allow_ownership_transfer=allow_ownership_transfer)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_copy_batch_check(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Returns the status of an asynchronous job for :meth:`files_copy_batch`.

        API Endpoint: /2/files/copy_batch_check
        Namespace: files
        Client type: user

        Args:
            async_job_id (str, required): Parameter for files_copy_batch_check

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            If success, it returns list of results for each entry.
            Route attributes:
            scope: files.content.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.files.RelocationBatchJobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.PollError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_copy_batch_check(async_job_id)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_copy_batch_check_v2(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Returns the status of an asynchronous job for

        API Endpoint: /2/files/copy_batch_check_v2
        Namespace: files
        Client type: user

        Args:
            async_job_id (str, required): Parameter for files_copy_batch_check_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`files_copy_batch_v2`. It returns list of results for each entry.
            Route attributes:
            scope: files.content.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.files.RelocationBatchV2JobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.PollError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_copy_batch_check_v2(async_job_id)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_copy_batch_v2(
        self,
        entries: str,
        autorename: str = False
    ) -> DropboxResponse:
        """Copy multiple files or folders to different locations at once in the

        API Endpoint: /2/files/copy_batch_v2
        Namespace: files
        Client type: user

        Args:
            entries (str, required): Parameter for files_copy_batch_v2
            autorename (str, optional): Parameter for files_copy_batch_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            user's Dropbox. This route will replace :meth:`files_copy_batch`. The
            main difference is this route will return status for each entry, while
            :meth:`files_copy_batch` raises failure if any entry fails. This route
            will either finish synchronously, or return a job ID and do the async
            copy job in background. Please use :meth:`files_copy_batch_check_v2` to
            check the job status.
            Route attributes:
            scope: files.content.write
            :param List[:class:`dropbox.files.RelocationPath`] entries: List of
            entries to be moved or copied. Each entry is
            :class:`dropbox.files.RelocationPath`.
            :param bool autorename: If there's a conflict with any file, have the
            Dropbox server try to autorename that file to avoid the conflict.
            :rtype: :class:`dropbox.files.RelocationBatchV2Launch`
        """
        client = await self._get_user_client()
        try:
            response = client.files_copy_batch_v2(entries, autorename=autorename)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_copy_reference_get(
        self,
        path: str
    ) -> DropboxResponse:
        """Get a copy reference to a file or folder. This reference string can be

        API Endpoint: /2/files/copy_reference_get
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_copy_reference_get

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            used to save that file or folder to another user's Dropbox by passing it
            to :meth:`files_copy_reference_save`.
            Route attributes:
            scope: files.content.write
            :param str path: The path to the file or folder you want to get a copy
            reference to.
            :rtype: :class:`dropbox.files.GetCopyReferenceResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.GetCopyReferenceError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_copy_reference_get(path)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_copy_reference_save(
        self,
        copy_reference: str,
        path: str
    ) -> DropboxResponse:
        """Save a copy reference returned by :meth:`files_copy_reference_get` to

        API Endpoint: /2/files/copy_reference_save
        Namespace: files
        Client type: user

        Args:
            copy_reference (str, required): Parameter for files_copy_reference_save
            path (str, required): Parameter for files_copy_reference_save

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            the user's Dropbox.
            Route attributes:
            scope: files.content.write
            :param str copy_reference: A copy reference returned by
            :meth:`files_copy_reference_get`.
            :param str path: Path in the user's Dropbox that is the destination.
            :rtype: :class:`dropbox.files.SaveCopyReferenceResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.SaveCopyReferenceError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_copy_reference_save(copy_reference, path)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_copy_v2(
        self,
        from_path: str,
        to_path: str,
        allow_shared_folder: str = False,
        autorename: str = False,
        allow_ownership_transfer: str = False
    ) -> DropboxResponse:
        """Copy a file or folder to a different location in the user's Dropbox. If

        API Endpoint: /2/files/copy_v2
        Namespace: files
        Client type: user

        Args:
            from_path (str, required): Parameter for files_copy_v2
            to_path (str, required): Parameter for files_copy_v2
            allow_shared_folder (str, optional): Parameter for files_copy_v2
            autorename (str, optional): Parameter for files_copy_v2
            allow_ownership_transfer (str, optional): Parameter for files_copy_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            the source path is a folder all its contents will be copied.
            Route attributes:
            scope: files.content.write
            :param bool allow_shared_folder: This flag has no effect.
            :param bool autorename: If there's a conflict, have the Dropbox server
            try to autorename the file to avoid the conflict.
            :param bool allow_ownership_transfer: Allow moves by owner even if it
            would result in an ownership transfer for the content being moved.
            This does not apply to copies.
            :rtype: :class:`dropbox.files.RelocationResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.RelocationError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_copy_v2(from_path, to_path, allow_shared_folder=allow_shared_folder, autorename=autorename, allow_ownership_transfer=allow_ownership_transfer)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_create_folder(
        self,
        path: str,
        autorename: str = False
    ) -> DropboxResponse:
        """Create a folder at a given path.

        API Endpoint: /2/files/create_folder
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_create_folder
            autorename (str, optional): Parameter for files_create_folder

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.content.write
            :param str path: Path in the user's Dropbox to create.
            :param bool autorename: If there's a conflict, have the Dropbox server
            try to autorename the folder to avoid the conflict.
            :rtype: :class:`dropbox.files.FolderMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.CreateFolderError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_create_folder(path, autorename=autorename)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_create_folder_batch(
        self,
        paths: str,
        autorename: str = False,
        force_async: str = False
    ) -> DropboxResponse:
        """Create multiple folders at once. This route is asynchronous for large

        API Endpoint: /2/files/create_folder_batch
        Namespace: files
        Client type: user

        Args:
            paths (str, required): Parameter for files_create_folder_batch
            autorename (str, optional): Parameter for files_create_folder_batch
            force_async (str, optional): Parameter for files_create_folder_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            batches, which returns a job ID immediately and runs the create folder
            batch asynchronously. Otherwise, creates the folders and returns the
            result synchronously for smaller inputs. You can force asynchronous
            behaviour by using the ``CreateFolderBatchArg.force_async`` flag.  Use
            :meth:`files_create_folder_batch_check` to check the job status.
            Route attributes:
            scope: files.content.write
            :param List[str] paths: List of paths to be created in the user's
            Dropbox. Duplicate path arguments in the batch are considered only
            once.
            :param bool autorename: If there's a conflict, have the Dropbox server
            try to autorename the folder to avoid the conflict.
            :param bool force_async: Whether to force the create to happen
            asynchronously.
            :rtype: :class:`dropbox.files.CreateFolderBatchLaunch`
        """
        client = await self._get_user_client()
        try:
            response = client.files_create_folder_batch(paths, autorename=autorename, force_async=force_async)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_create_folder_batch_check(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Returns the status of an asynchronous job for

        API Endpoint: /2/files/create_folder_batch_check
        Namespace: files
        Client type: user

        Args:
            async_job_id (str, required): Parameter for files_create_folder_batch_check

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`files_create_folder_batch`. If success, it returns list of result
            for each entry.
            Route attributes:
            scope: files.content.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.files.CreateFolderBatchJobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.PollError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_create_folder_batch_check(async_job_id)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_create_folder_v2(
        self,
        path: str,
        autorename: str = False
    ) -> DropboxResponse:
        """Create a folder at a given path.

        API Endpoint: /2/files/create_folder_v2
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_create_folder_v2
            autorename (str, optional): Parameter for files_create_folder_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.content.write
            :param str path: Path in the user's Dropbox to create.
            :param bool autorename: If there's a conflict, have the Dropbox server
            try to autorename the folder to avoid the conflict.
            :rtype: :class:`dropbox.files.CreateFolderResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.CreateFolderError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_create_folder_v2(path, autorename=autorename)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_delete(
        self,
        path: str,
        parent_rev: Optional[str] = None
    ) -> DropboxResponse:
        """Delete the file or folder at a given path. If the path is a folder, all

        API Endpoint: /2/files/delete
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_delete
            parent_rev (str, optional): Parameter for files_delete

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            its contents will be deleted too. A successful response indicates that
            the file or folder was deleted. The returned metadata will be the
            corresponding :class:`dropbox.files.FileMetadata` or
            :class:`dropbox.files.FolderMetadata` for the item at time of deletion,
            and not a :class:`dropbox.files.DeletedMetadata` object.
            Route attributes:
            scope: files.content.write
            :param str path: Path in the user's Dropbox to delete.
            :param Nullable[str] parent_rev: Perform delete if given "rev" matches
            the existing file's latest "rev". This field does not support
            deleting a folder.
            :rtype: :class:`dropbox.files.Metadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.DeleteError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_delete(path, parent_rev=parent_rev)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_delete_batch(
        self,
        entries: str
    ) -> DropboxResponse:
        """Delete multiple files/folders at once. This route is asynchronous, which

        API Endpoint: /2/files/delete_batch
        Namespace: files
        Client type: user

        Args:
            entries (str, required): Parameter for files_delete_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            returns a job ID immediately and runs the delete batch asynchronously.
            Use :meth:`files_delete_batch_check` to check the job status.
            Route attributes:
            scope: files.content.write
            :type entries: List[:class:`dropbox.files.DeleteArg`]
            :rtype: :class:`dropbox.files.DeleteBatchLaunch`
        """
        client = await self._get_user_client()
        try:
            response = client.files_delete_batch(entries)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_delete_batch_check(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Returns the status of an asynchronous job for

        API Endpoint: /2/files/delete_batch_check
        Namespace: files
        Client type: user

        Args:
            async_job_id (str, required): Parameter for files_delete_batch_check

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`files_delete_batch`. If success, it returns list of result for
            each entry.
            Route attributes:
            scope: files.content.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.files.DeleteBatchJobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.PollError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_delete_batch_check(async_job_id)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_delete_v2(
        self,
        path: str,
        parent_rev: Optional[str] = None
    ) -> DropboxResponse:
        """Delete the file or folder at a given path. If the path is a folder, all

        API Endpoint: /2/files/delete_v2
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_delete_v2
            parent_rev (str, optional): Parameter for files_delete_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            its contents will be deleted too. A successful response indicates that
            the file or folder was deleted. The returned metadata will be the
            corresponding :class:`dropbox.files.FileMetadata` or
            :class:`dropbox.files.FolderMetadata` for the item at time of deletion,
            and not a :class:`dropbox.files.DeletedMetadata` object.
            Route attributes:
            scope: files.content.write
            :param str path: Path in the user's Dropbox to delete.
            :param Nullable[str] parent_rev: Perform delete if given "rev" matches
            the existing file's latest "rev". This field does not support
            deleting a folder.
            :rtype: :class:`dropbox.files.DeleteResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.DeleteError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_delete_v2(path, parent_rev=parent_rev)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_download(
        self,
        path: str,
        rev: Optional[str] = None
    ) -> DropboxResponse:
        """Download a file from a user's Dropbox.

        API Endpoint: /2/files/download
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_download
            rev (str, optional): Parameter for files_download

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.content.read
            :param str path: The path of the file to download.
            :param Nullable[str] rev: Please specify revision in ``path`` instead.
            :rtype: (:class:`dropbox.files.FileMetadata`,
            :class:`requests.models.Response`)
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.DownloadError`
            If you do not consume the entire response body, then you must call close
            on the response object, otherwise you will max out your available
            connections. We recommend using the `contextlib.closing
            <https://docs.python.org/2/library/contextlib.html#contextlib.closing>`_
            context manager to ensure this.
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_download(path, rev=rev))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_download_to_file(
        self,
        download_path: str,
        path: str,
        rev: Optional[str] = None
    ) -> DropboxResponse:
        """Download a file from a user's Dropbox.

        API Endpoint: /2/files/download_to_file
        Namespace: files
        Client type: user

        Args:
            download_path (str, required): Parameter for files_download_to_file
            path (str, required): Parameter for files_download_to_file
            rev (str, optional): Parameter for files_download_to_file

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.content.read
            :param str download_path: Path on local machine to save file.
            :param str path: The path of the file to download.
            :param Nullable[str] rev: Please specify revision in ``path`` instead.
            :rtype: :class:`dropbox.files.FileMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.DownloadError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_download_to_file(download_path, path, rev=rev)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_download_zip(
        self,
        path: str
    ) -> DropboxResponse:
        """Download a folder from the user's Dropbox, as a zip file. The folder

        API Endpoint: /2/files/download_zip
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_download_zip

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            must be less than 20 GB in size and any single file within must be less
            than 4 GB in size. The resulting zip must have fewer than 10,000 total
            file and folder entries, including the top level folder. The input
            cannot be a single file. Note: this endpoint does not support HTTP range
            requests.
            Route attributes:
            scope: files.content.read
            :param str path: The path of the folder to download.
            :rtype: (:class:`dropbox.files.DownloadZipResult`,
            :class:`requests.models.Response`)
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.DownloadZipError`
            If you do not consume the entire response body, then you must call close
            on the response object, otherwise you will max out your available
            connections. We recommend using the `contextlib.closing
            <https://docs.python.org/2/library/contextlib.html#contextlib.closing>`_
            context manager to ensure this.
        """
        client = await self._get_user_client()
        try:
            response = client.files_download_zip(path)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_download_zip_to_file(
        self,
        download_path: str,
        path: str
    ) -> DropboxResponse:
        """Download a folder from the user's Dropbox, as a zip file. The folder

        API Endpoint: /2/files/download_zip_to_file
        Namespace: files
        Client type: user

        Args:
            download_path (str, required): Parameter for files_download_zip_to_file
            path (str, required): Parameter for files_download_zip_to_file

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            must be less than 20 GB in size and any single file within must be less
            than 4 GB in size. The resulting zip must have fewer than 10,000 total
            file and folder entries, including the top level folder. The input
            cannot be a single file. Note: this endpoint does not support HTTP range
            requests.
            Route attributes:
            scope: files.content.read
            :param str download_path: Path on local machine to save file.
            :param str path: The path of the folder to download.
            :rtype: :class:`dropbox.files.DownloadZipResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.DownloadZipError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_download_zip_to_file(download_path, path)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_export(
        self,
        path: str,
        export_format: Optional[str] = None
    ) -> DropboxResponse:
        """Export a file from a user's Dropbox. This route only supports exporting

        API Endpoint: /2/files/export
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_export
            export_format (str, optional): Parameter for files_export

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            files that cannot be downloaded directly  and whose
            ``ExportResult.file_metadata`` has ``ExportInfo.export_as`` populated.
            Route attributes:
            scope: files.content.read
            :param str path: The path of the file to be exported.
            :param Nullable[str] export_format: The file format to which the file
            should be exported. This must be one of the formats listed in the
            file's export_options returned by :meth:`files_get_metadata`. If
            none is specified, the default format (specified in export_as in
            file metadata) will be used.
            :rtype: (:class:`dropbox.files.ExportResult`,
            :class:`requests.models.Response`)
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.ExportError`
            If you do not consume the entire response body, then you must call close
            on the response object, otherwise you will max out your available
            connections. We recommend using the `contextlib.closing
            <https://docs.python.org/2/library/contextlib.html#contextlib.closing>`_
            context manager to ensure this.
        """
        client = await self._get_user_client()
        try:
            response = client.files_export(path, export_format=export_format)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_export_to_file(
        self,
        download_path: str,
        path: str,
        export_format: Optional[str] = None
    ) -> DropboxResponse:
        """Export a file from a user's Dropbox. This route only supports exporting

        API Endpoint: /2/files/export_to_file
        Namespace: files
        Client type: user

        Args:
            download_path (str, required): Parameter for files_export_to_file
            path (str, required): Parameter for files_export_to_file
            export_format (str, optional): Parameter for files_export_to_file

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            files that cannot be downloaded directly  and whose
            ``ExportResult.file_metadata`` has ``ExportInfo.export_as`` populated.
            Route attributes:
            scope: files.content.read
            :param str download_path: Path on local machine to save file.
            :param str path: The path of the file to be exported.
            :param Nullable[str] export_format: The file format to which the file
            should be exported. This must be one of the formats listed in the
            file's export_options returned by :meth:`files_get_metadata`. If
            none is specified, the default format (specified in export_as in
            file metadata) will be used.
            :rtype: :class:`dropbox.files.ExportResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.ExportError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_export_to_file(download_path, path, export_format=export_format)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_get_file_lock_batch(
        self,
        entries: str
    ) -> DropboxResponse:
        """Return the lock metadata for the given list of paths.

        API Endpoint: /2/files/get_file_lock_batch
        Namespace: files
        Client type: user

        Args:
            entries (str, required): Parameter for files_get_file_lock_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.content.read
            :param List[:class:`dropbox.files.LockFileArg`] entries: List of
            'entries'. Each 'entry' contains a path of the file which will be
            locked or queried. Duplicate path arguments in the batch are
            considered only once.
            :rtype: :class:`dropbox.files.LockFileBatchResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.LockFileError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_get_file_lock_batch(entries)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_get_metadata(
        self,
        path: str,
        include_media_info: str = False,
        include_deleted: str = False,
        include_has_explicit_shared_members: str = False,
        include_property_groups: Optional[str] = None,
        team_member_id: Optional[str] = None,
        team_folder_id: Optional[str] = None,
    ) -> DropboxResponse:
        """Returns the metadata for a file or folder. Note: Metadata for the root

        API Endpoint: /2/files/get_metadata
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_get_metadata
            include_media_info (str, optional): Parameter for files_get_metadata
            include_deleted (str, optional): Parameter for files_get_metadata
            include_has_explicit_shared_members (str, optional): Parameter for files_get_metadata
            include_property_groups (str, optional): Parameter for files_get_metadata

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            folder is unsupported.
            Route attributes:
            scope: files.metadata.read
            :param str path: The path of a file or folder on Dropbox.
            :param bool include_media_info: If true, ``FileMetadata.media_info`` is
            set for photo and video.
            :param bool include_deleted: If true,
            :class:`dropbox.files.DeletedMetadata` will be returned for deleted
            file or folder, otherwise ``LookupError.not_found`` will be
            returned.
            :param bool include_has_explicit_shared_members: If true, the results
            will include a flag for each file indicating whether or not  that
            file has any explicit members.
            :param Nullable[:class:`dropbox.files.TemplateFilterBase`]
            include_property_groups: If set to a valid list of template IDs,
            ``FileMetadata.property_groups`` is set if there exists property
            data associated with the file and each of the listed templates.
            :rtype: :class:`dropbox.files.Metadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.GetMetadataError`
        """
        try:
            client = await self._get_user_client(team_member_id)

            client_to_use = client
            if team_folder_id:
                client_to_use = client.with_path_root(
                    dropbox.common.PathRoot.namespace_id(team_folder_id)
                )
            response = client_to_use.files_get_metadata(path, include_media_info=include_media_info, include_deleted=include_deleted, include_has_explicit_shared_members=include_has_explicit_shared_members, include_property_groups=include_property_groups)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_get_preview(
        self,
        path: str,
        rev: Optional[str] = None
    ) -> DropboxResponse:
        """Get a preview for a file. Currently, PDF previews are generated for

        API Endpoint: /2/files/get_preview
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_get_preview
            rev (str, optional): Parameter for files_get_preview

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            files with the following extensions: .ai, .doc, .docm, .docx, .eps,
            .gdoc, .gslides, .odp, .odt, .pps, .ppsm, .ppsx, .ppt, .pptm, .pptx,
            .rtf. HTML previews are generated for files with the following
            extensions: .csv, .ods, .xls, .xlsm, .gsheet, .xlsx. Other formats will
            return an unsupported extension error.
            Route attributes:
            scope: files.content.read
            :param str path: The path of the file to preview.
            :param Nullable[str] rev: Please specify revision in ``path`` instead.
            :rtype: (:class:`dropbox.files.FileMetadata`,
            :class:`requests.models.Response`)
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.PreviewError`
            If you do not consume the entire response body, then you must call close
            on the response object, otherwise you will max out your available
            connections. We recommend using the `contextlib.closing
            <https://docs.python.org/2/library/contextlib.html#contextlib.closing>`_
            context manager to ensure this.
        """
        client = await self._get_user_client()
        try:
            response = client.files_get_preview(path, rev=rev)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_get_preview_to_file(
        self,
        download_path: str,
        path: str,
        rev: Optional[str] = None
    ) -> DropboxResponse:
        """Get a preview for a file. Currently, PDF previews are generated for

        API Endpoint: /2/files/get_preview_to_file
        Namespace: files
        Client type: user

        Args:
            download_path (str, required): Parameter for files_get_preview_to_file
            path (str, required): Parameter for files_get_preview_to_file
            rev (str, optional): Parameter for files_get_preview_to_file

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            files with the following extensions: .ai, .doc, .docm, .docx, .eps,
            .gdoc, .gslides, .odp, .odt, .pps, .ppsm, .ppsx, .ppt, .pptm, .pptx,
            .rtf. HTML previews are generated for files with the following
            extensions: .csv, .ods, .xls, .xlsm, .gsheet, .xlsx. Other formats will
            return an unsupported extension error.
            Route attributes:
            scope: files.content.read
            :param str download_path: Path on local machine to save file.
            :param str path: The path of the file to preview.
            :param Nullable[str] rev: Please specify revision in ``path`` instead.
            :rtype: :class:`dropbox.files.FileMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.PreviewError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_get_preview_to_file(download_path, path, rev=rev)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_get_temporary_link(
        self,
        path: str,
        team_folder_id: Optional[str] = None,
        team_member_id: Optional[str] = None,
    ) -> DropboxResponse:
        """Get a temporary link to stream content of a file. This link will expire

        API Endpoint: /2/files/get_temporary_link
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_get_temporary_link

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            in four hours and afterwards you will get 410 Gone. This URL should not
            be used to display content directly in the browser. The Content-Type of
            the link is determined automatically by the file's mime type.
            Route attributes:
            scope: files.content.read
            :param str path: The path to the file you want a temporary link to.
            :rtype: :class:`dropbox.files.GetTemporaryLinkResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.GetTemporaryLinkError`
        """
        # client = await self._get_user_client()
        try:
            client = await self._get_user_client(team_member_id)

            client_to_use = client
            if team_folder_id:
                client_to_use = client.with_path_root(
                    dropbox.common.PathRoot.namespace_id(team_folder_id)
                )

            response = client_to_use.files_get_temporary_link(path)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_get_temporary_upload_link(
        self,
        commit_info: str,
        duration: str = 14400.0
    ) -> DropboxResponse:
        """Get a one-time use temporary upload link to upload a file to a Dropbox

        API Endpoint: /2/files/get_temporary_upload_link
        Namespace: files
        Client type: user

        Args:
            commit_info (str, required): Parameter for files_get_temporary_upload_link
            duration (str, optional): Parameter for files_get_temporary_upload_link

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            location.  This endpoint acts as a delayed :meth:`files_upload`. The
            returned temporary upload link may be used to make a POST request with
            the data to be uploaded. The upload will then be perfomed with the
            :class:`dropbox.files.CommitInfo` previously provided to
            :meth:`files_get_temporary_upload_link` but evaluated only upon
            consumption. Hence, errors stemming from invalid
            :class:`dropbox.files.CommitInfo` with respect to the state of the
            user's Dropbox will only be communicated at consumption time.
            Additionally, these errors are surfaced as generic HTTP 409 Conflict
            responses, potentially hiding issue details. The maximum temporary
            upload link duration is 4 hours. Upon consumption or expiration, a new
            link will have to be generated. Multiple links may exist for a specific
            upload path at any given time.  The POST request on the temporary upload
            link must have its Content-Type set to "application/octet-stream".
            Example temporary upload link consumption request:  curl -X POST
            https://content.dropboxapi.com/apitul/1/bNi2uIYF51cVBND --header
            "Content-Type: application/octet-stream" --data-binary @local_file.txt
            A successful temporary upload link consumption request returns the
            content hash of the uploaded data in JSON format.  Example successful
            temporary upload link consumption response: {"content-hash":
            "599d71033d700ac892a0e48fa61b125d2f5994"}  An unsuccessful temporary
            upload link consumption request returns any of the following status
            codes:  HTTP 400 Bad Request: Content-Type is not one of
            application/octet-stream and text/plain or request is invalid. HTTP 409
            Conflict: The temporary upload link does not exist or is currently
            unavailable, the upload failed, or another error happened. HTTP 410
            Gone: The temporary upload link is expired or consumed.  Example
            unsuccessful temporary upload link consumption response: Temporary
            upload link has been recently consumed.
            Route attributes:
            scope: files.content.write
            :param commit_info: Contains the path and other optional modifiers for
            the future upload commit. Equivalent to the parameters provided to
            :meth:`files_upload`.
            :type commit_info: :class:`dropbox.files.CommitInfo`
            :param float duration: How long before this link expires, in seconds.
            Attempting to start an upload with this link longer than this period
            of time after link creation will result in an error.
            :rtype: :class:`dropbox.files.GetTemporaryUploadLinkResult`
        """
        client = await self._get_user_client()
        try:
            response = client.files_get_temporary_upload_link(commit_info, duration=duration)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_get_thumbnail(
        self,
        path: str,
        format: str = ThumbnailFormat('jpeg', None),
        size: str = ThumbnailSize('w64h64', None),
        mode: str = ThumbnailMode('strict', None)
    ) -> DropboxResponse:
        """Get a thumbnail for an image. This method currently supports files with

        API Endpoint: /2/files/get_thumbnail
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_get_thumbnail
            format (str, optional): Parameter for files_get_thumbnail
            size (str, optional): Parameter for files_get_thumbnail
            mode (str, optional): Parameter for files_get_thumbnail

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            the following file extensions: jpg, jpeg, png, tiff, tif, gif, webp, ppm
            and bmp. Photos that are larger than 20MB in size won't be converted to
            a thumbnail.
            Route attributes:
            scope: files.content.read
            :param str path: The path to the image file you want to thumbnail.
            :param format: The format for the thumbnail image, jpeg (default) or
            png. For  images that are photos, jpeg should be preferred, while
            png is  better for screenshots and digital arts.
            :type format: :class:`dropbox.files.ThumbnailFormat`
            :param size: The size for the thumbnail image.
            :type size: :class:`dropbox.files.ThumbnailSize`
            :param mode: How to resize and crop the image to achieve the desired
            size.
            :type mode: :class:`dropbox.files.ThumbnailMode`
            :rtype: (:class:`dropbox.files.FileMetadata`,
            :class:`requests.models.Response`)
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.ThumbnailError`
            If you do not consume the entire response body, then you must call close
            on the response object, otherwise you will max out your available
            connections. We recommend using the `contextlib.closing
            <https://docs.python.org/2/library/contextlib.html#contextlib.closing>`_
            context manager to ensure this.
        """
        client = await self._get_user_client()
        try:
            response = client.files_get_thumbnail(path, format=format, size=size, mode=mode)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_get_thumbnail_batch(
        self,
        entries: str
    ) -> DropboxResponse:
        """Get thumbnails for a list of images. We allow up to 25 thumbnails in a

        API Endpoint: /2/files/get_thumbnail_batch
        Namespace: files
        Client type: user

        Args:
            entries (str, required): Parameter for files_get_thumbnail_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            single batch. This method currently supports files with the following
            file extensions: jpg, jpeg, png, tiff, tif, gif, webp, ppm and bmp.
            Photos that are larger than 20MB in size won't be converted to a
            thumbnail.
            Route attributes:
            scope: files.content.read
            :param List[:class:`dropbox.files.ThumbnailArg`] entries: List of files
            to get thumbnails.
            :rtype: :class:`dropbox.files.GetThumbnailBatchResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.GetThumbnailBatchError`
        """
        client = await self._get_user_client()
        try:
            response = client.files_get_thumbnail_batch(entries)
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_get_thumbnail_to_file(
        self,
        download_path: str,
        path: str,
        format: str = ThumbnailFormat('jpeg', None),
        size: str = ThumbnailSize('w64h64', None),
        mode: str = ThumbnailMode('strict', None)
    ) -> DropboxResponse:
        """Get a thumbnail for an image. This method currently supports files with

        API Endpoint: /2/files/get_thumbnail_to_file
        Namespace: files
        Client type: user

        Args:
            download_path (str, required): Parameter for files_get_thumbnail_to_file
            path (str, required): Parameter for files_get_thumbnail_to_file
            format (str, optional): Parameter for files_get_thumbnail_to_file
            size (str, optional): Parameter for files_get_thumbnail_to_file
            mode (str, optional): Parameter for files_get_thumbnail_to_file

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            the following file extensions: jpg, jpeg, png, tiff, tif, gif, webp, ppm
            and bmp. Photos that are larger than 20MB in size won't be converted to
            a thumbnail.
            Route attributes:
            scope: files.content.read
            :param str download_path: Path on local machine to save file.
            :param str path: The path to the image file you want to thumbnail.
            :param format: The format for the thumbnail image, jpeg (default) or
            png. For  images that are photos, jpeg should be preferred, while
            png is  better for screenshots and digital arts.
            :type format: :class:`dropbox.files.ThumbnailFormat`
            :param size: The size for the thumbnail image.
            :type size: :class:`dropbox.files.ThumbnailSize`
            :param mode: How to resize and crop the image to achieve the desired
            size.
            :type mode: :class:`dropbox.files.ThumbnailMode`
            :rtype: :class:`dropbox.files.FileMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.ThumbnailError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_get_thumbnail_to_file(download_path, path, format=format, size=size, mode=mode))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_get_thumbnail_to_file_v2(
        self,
        download_path: str,
        resource: str,
        format: str = ThumbnailFormat('jpeg', None),
        size: str = ThumbnailSize('w64h64', None),
        mode: str = ThumbnailMode('strict', None)
    ) -> DropboxResponse:
        """Get a thumbnail for an image. This method currently supports files with

        API Endpoint: /2/files/get_thumbnail_to_file_v2
        Namespace: files
        Client type: user

        Args:
            download_path (str, required): Parameter for files_get_thumbnail_to_file_v2
            resource (str, required): Parameter for files_get_thumbnail_to_file_v2
            format (str, optional): Parameter for files_get_thumbnail_to_file_v2
            size (str, optional): Parameter for files_get_thumbnail_to_file_v2
            mode (str, optional): Parameter for files_get_thumbnail_to_file_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            the following file extensions: jpg, jpeg, png, tiff, tif, gif, webp, ppm
            and bmp. Photos that are larger than 20MB in size won't be converted to
            a thumbnail.
            Route attributes:
            scope: files.content.read
            :param str download_path: Path on local machine to save file.
            :param resource: Information specifying which file to preview. This
            could be a path to a file, a shared link pointing to a file, or a
            shared link pointing to a folder, with a relative path.
            :type resource: :class:`dropbox.files.PathOrLink`
            :param format: The format for the thumbnail image, jpeg (default) or
            png. For  images that are photos, jpeg should be preferred, while
            png is  better for screenshots and digital arts.
            :type format: :class:`dropbox.files.ThumbnailFormat`
            :param size: The size for the thumbnail image.
            :type size: :class:`dropbox.files.ThumbnailSize`
            :param mode: How to resize and crop the image to achieve the desired
            size.
            :type mode: :class:`dropbox.files.ThumbnailMode`
            :rtype: :class:`dropbox.files.PreviewResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.ThumbnailV2Error`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_get_thumbnail_to_file_v2(download_path, resource, format=format, size=size, mode=mode))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_get_thumbnail_v2(
        self,
        resource: str,
        format: str = ThumbnailFormat('jpeg', None),
        size: str = ThumbnailSize('w64h64', None),
        mode: str = ThumbnailMode('strict', None)
    ) -> DropboxResponse:
        """Get a thumbnail for an image. This method currently supports files with

        API Endpoint: /2/files/get_thumbnail_v2
        Namespace: files
        Client type: user

        Args:
            resource (str, required): Parameter for files_get_thumbnail_v2
            format (str, optional): Parameter for files_get_thumbnail_v2
            size (str, optional): Parameter for files_get_thumbnail_v2
            mode (str, optional): Parameter for files_get_thumbnail_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            the following file extensions: jpg, jpeg, png, tiff, tif, gif, webp, ppm
            and bmp. Photos that are larger than 20MB in size won't be converted to
            a thumbnail.
            Route attributes:
            scope: files.content.read
            :param resource: Information specifying which file to preview. This
            could be a path to a file, a shared link pointing to a file, or a
            shared link pointing to a folder, with a relative path.
            :type resource: :class:`dropbox.files.PathOrLink`
            :param format: The format for the thumbnail image, jpeg (default) or
            png. For  images that are photos, jpeg should be preferred, while
            png is  better for screenshots and digital arts.
            :type format: :class:`dropbox.files.ThumbnailFormat`
            :param size: The size for the thumbnail image.
            :type size: :class:`dropbox.files.ThumbnailSize`
            :param mode: How to resize and crop the image to achieve the desired
            size.
            :type mode: :class:`dropbox.files.ThumbnailMode`
            :rtype: (:class:`dropbox.files.PreviewResult`,
            :class:`requests.models.Response`)
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.ThumbnailV2Error`
            If you do not consume the entire response body, then you must call close
            on the response object, otherwise you will max out your available
            connections. We recommend using the `contextlib.closing
            <https://docs.python.org/2/library/contextlib.html#contextlib.closing>`_
            context manager to ensure this.
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_get_thumbnail_v2(resource, format=format, size=size, mode=mode))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_list_folder(
        self,
        path: str,
        recursive: str = False,
        include_media_info: str = False,
        include_deleted: str = False,
        include_has_explicit_shared_members: str = False,
        include_mounted_folders: str = True,
        limit: Optional[str] = None,
        shared_link: Optional[str] = None,
        include_property_groups: Optional[str] = None,
        include_non_downloadable_files: str = True,
        team_folder_id: Optional[str] = None,  # New parameter
        team_member_id: Optional[str] = None,  # New parameter
    ) -> DropboxResponse:
        """Starts returning the contents of a folder. If the result's

        API Endpoint: /2/files/list_folder
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_list_folder
            recursive (str, optional): Parameter for files_list_folder
            include_media_info (str, optional): Parameter for files_list_folder
            include_deleted (str, optional): Parameter for files_list_folder
            include_has_explicit_shared_members (str, optional): Parameter for files_list_folder
            include_mounted_folders (str, optional): Parameter for files_list_folder
            limit (str, optional): Parameter for files_list_folder
            shared_link (str, optional): Parameter for files_list_folder
            include_property_groups (str, optional): Parameter for files_list_folder
            include_non_downloadable_files (str, optional): Parameter for files_list_folder

            !!! Important
            team_folder_id (str, optional): Parameter for files_list_folder that specifies the folder to list from `namespaceID`

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            ``ListFolderResult.has_more`` field is ``True``, call
            :meth:`files_list_folder_continue` with the returned
            ``ListFolderResult.cursor`` to retrieve more entries. If you're using
            ``ListFolderArg.recursive`` set to ``True`` to keep a local cache of the
            contents of a Dropbox account, iterate through each entry in order and
            process them as follows to keep your local state in sync: For each
            :class:`dropbox.files.FileMetadata`, store the new entry at the given
            path in your local state. If the required parent folders don't exist
            yet, create them. If there's already something else at the given path,
            replace it and remove all its children. For each
            :class:`dropbox.files.FolderMetadata`, store the new entry at the given
            path in your local state. If the required parent folders don't exist
            yet, create them. If there's already something else at the given path,
            replace it but leave the children as they are. Check the new entry's
            ``FolderSharingInfo.read_only`` and set all its children's read-only
            statuses to match. For each :class:`dropbox.files.DeletedMetadata`, if
            your local state has something at the given path, remove it and all its
            children. If there's nothing at the given path, ignore this entry. Note:
            :class:`dropbox.auth.RateLimitError` may be returned if multiple
            :meth:`files_list_folder` or :meth:`files_list_folder_continue` calls
            with same parameters are made simultaneously by same API app for same
            user. If your app implements retry logic, please hold off the retry
            until the previous request finishes.
            Route attributes:
            scope: files.metadata.read
            :param str path: A unique identifier for the file.
            :param bool recursive: If true, the list folder operation will be
            applied recursively to all subfolders and the response will contain
            contents of all subfolders.
            :param bool include_media_info: If true, ``FileMetadata.media_info`` is
            set for photo and video. This parameter will no longer have an
            effect starting December 2, 2019.
            :param bool include_deleted: If true, the results will include entries
            for files and folders that used to exist but were deleted.
            :param bool include_has_explicit_shared_members: If true, the results
            will include a flag for each file indicating whether or not  that
            file has any explicit members.
            :param bool include_mounted_folders: If true, the results will include
            entries under mounted folders which includes app folder, shared
            folder and team folder.
            :param Nullable[int] limit: The maximum number of results to return per
            request. Note: This is an approximate number and there can be
            slightly more entries returned in some cases.
            :param Nullable[:class:`dropbox.files.SharedLink`] shared_link: A shared
            link to list the contents of. If the link is password-protected, the
            password must be provided. If this field is present,
            ``ListFolderArg.path`` will be relative to root of the shared link.
            Only non-recursive mode is supported for shared link.
            :param Nullable[:class:`dropbox.files.TemplateFilterBase`]
            include_property_groups: If set to a valid list of template IDs,
            ``FileMetadata.property_groups`` is set if there exists property
            data associated with the file and each of the listed templates.
            :param bool include_non_downloadable_files: If true, include files that
            are not downloadable, i.e. Google Docs.
            :rtype: :class:`dropbox.files.ListFolderResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.ListFolderError`
        """
        try:
            # Get the base client instance
            client = await self._get_user_client(team_member_id)

            # Conditionally scope the client to a team folder
            client_to_use = client
            if team_folder_id:
                client_to_use = client.with_path_root(
                    dropbox.common.PathRoot.namespace_id(team_folder_id)
                )

            # Run the synchronous SDK call in a separate thread
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client_to_use.files_list_folder(
                    path=path,
                    recursive=recursive,
                    include_media_info=include_media_info,
                    include_deleted=include_deleted,
                    include_has_explicit_shared_members=include_has_explicit_shared_members,
                    include_mounted_folders=include_mounted_folders,
                    limit=limit,
                    shared_link=shared_link,
                    include_property_groups=include_property_groups,
                    include_non_downloadable_files=include_non_downloadable_files
                )
            )
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_list_folder_continue(
        self,
        cursor: str,
        team_member_id: Optional[str] = None,
        team_folder_id: Optional[str] = None,
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from :meth:`files_list_folder`, use

        API Endpoint: /2/files/list_folder_continue
        Namespace: files
        Client type: user

        Args:
            cursor (str, required): Parameter for files_list_folder_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to paginate through all files and retrieve updates to the folder,
            following the same rules as documented for :meth:`files_list_folder`.
            Route attributes:
            scope: files.metadata.read
            :param str cursor: The cursor returned by your last call to
            :meth:`files_list_folder` or :meth:`files_list_folder_continue`.
            :rtype: :class:`dropbox.files.ListFolderResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.ListFolderContinueError`
        """
        try:
            # Get the base client instance for the correct user
            client = await self._get_user_client(team_member_id)

            # Conditionally scope the client to the team folder's namespace
            # This is CRUCIAL to avoid the 'reset' error
            client_to_use = client
            if team_folder_id:
                client_to_use = client.with_path_root(
                    dropbox.common.PathRoot.namespace_id(team_folder_id)
                )

            # Run the synchronous SDK call in a separate thread
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client_to_use.files_list_folder_continue(cursor)
            )
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_list_folder_get_latest_cursor(
        self,
        path: str,
        recursive: str = False,
        include_media_info: str = False,
        include_deleted: str = False,
        include_has_explicit_shared_members: str = False,
        include_mounted_folders: str = True,
        limit: Optional[str] = None,
        shared_link: Optional[str] = None,
        include_property_groups: Optional[str] = None,
        include_non_downloadable_files: str = True
    ) -> DropboxResponse:
        """A way to quickly get a cursor for the folder's state. Unlike

        API Endpoint: /2/files/list_folder_get_latest_cursor
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_list_folder_get_latest_cursor
            recursive (str, optional): Parameter for files_list_folder_get_latest_cursor
            include_media_info (str, optional): Parameter for files_list_folder_get_latest_cursor
            include_deleted (str, optional): Parameter for files_list_folder_get_latest_cursor
            include_has_explicit_shared_members (str, optional): Parameter for files_list_folder_get_latest_cursor
            include_mounted_folders (str, optional): Parameter for files_list_folder_get_latest_cursor
            limit (str, optional): Parameter for files_list_folder_get_latest_cursor
            shared_link (str, optional): Parameter for files_list_folder_get_latest_cursor
            include_property_groups (str, optional): Parameter for files_list_folder_get_latest_cursor
            include_non_downloadable_files (str, optional): Parameter for files_list_folder_get_latest_cursor

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`files_list_folder`, :meth:`files_list_folder_get_latest_cursor`
            doesn't return any entries. This endpoint is for app which only needs to
            know about new files and modifications and doesn't need to know about
            files that already exist in Dropbox.
            Route attributes:
            scope: files.metadata.read
            :param str path: A unique identifier for the file.
            :param bool recursive: If true, the list folder operation will be
            applied recursively to all subfolders and the response will contain
            contents of all subfolders.
            :param bool include_media_info: If true, ``FileMetadata.media_info`` is
            set for photo and video. This parameter will no longer have an
            effect starting December 2, 2019.
            :param bool include_deleted: If true, the results will include entries
            for files and folders that used to exist but were deleted.
            :param bool include_has_explicit_shared_members: If true, the results
            will include a flag for each file indicating whether or not  that
            file has any explicit members.
            :param bool include_mounted_folders: If true, the results will include
            entries under mounted folders which includes app folder, shared
            folder and team folder.
            :param Nullable[int] limit: The maximum number of results to return per
            request. Note: This is an approximate number and there can be
            slightly more entries returned in some cases.
            :param Nullable[:class:`dropbox.files.SharedLink`] shared_link: A shared
            link to list the contents of. If the link is password-protected, the
            password must be provided. If this field is present,
            ``ListFolderArg.path`` will be relative to root of the shared link.
            Only non-recursive mode is supported for shared link.
            :param Nullable[:class:`dropbox.files.TemplateFilterBase`]
            include_property_groups: If set to a valid list of template IDs,
            ``FileMetadata.property_groups`` is set if there exists property
            data associated with the file and each of the listed templates.
            :param bool include_non_downloadable_files: If true, include files that
            are not downloadable, i.e. Google Docs.
            :rtype: :class:`dropbox.files.ListFolderGetLatestCursorResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.ListFolderError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_list_folder_get_latest_cursor(path, recursive=recursive, include_media_info=include_media_info, include_deleted=include_deleted, include_has_explicit_shared_members=include_has_explicit_shared_members, include_mounted_folders=include_mounted_folders, limit=limit, shared_link=shared_link, include_property_groups=include_property_groups, include_non_downloadable_files=include_non_downloadable_files))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_list_folder_longpoll(
        self,
        cursor: str,
        timeout: str = 30
    ) -> DropboxResponse:
        """A longpoll endpoint to wait for changes on an account. In conjunction

        API Endpoint: /2/files/list_folder_longpoll
        Namespace: files
        Client type: user

        Args:
            cursor (str, required): Parameter for files_list_folder_longpoll
            timeout (str, optional): Parameter for files_list_folder_longpoll

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            with :meth:`files_list_folder_continue`, this call gives you a
            low-latency way to monitor an account for file changes. The connection
            will block until there are changes available or a timeout occurs. This
            endpoint is useful mostly for client-side apps. If you're looking for
            server-side notifications, check out our `webhooks documentation
            <https://www.dropbox.com/developers/reference/webhooks>`_.
            Route attributes:
            scope: files.metadata.read
            :param str cursor: A cursor as returned by :meth:`files_list_folder` or
            :meth:`files_list_folder_continue`. Cursors retrieved by setting
            ``ListFolderArg.include_media_info`` to ``True`` are not supported.
            :param int timeout: A timeout in seconds. The request will block for at
            most this length of time, plus up to 90 seconds of random jitter
            added to avoid the thundering herd problem. Care should be taken
            when using this parameter, as some network infrastructure does not
            support long timeouts.
            :rtype: :class:`dropbox.files.ListFolderLongpollResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.ListFolderLongpollError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_list_folder_longpoll(cursor, timeout=timeout))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_list_revisions(
        self,
        path: str,
        mode: str = ListRevisionsMode('path', None),
        limit: str = 10
    ) -> DropboxResponse:
        """Returns revisions for files based on a file path or a file id. The file

        API Endpoint: /2/files/list_revisions
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_list_revisions
            mode (str, optional): Parameter for files_list_revisions
            limit (str, optional): Parameter for files_list_revisions

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            path or file id is identified from the latest file entry at the given
            file path or id. This end point allows your app to query either by file
            path or file id by setting the mode parameter appropriately. In the
            ``ListRevisionsMode.path`` (default) mode, all revisions at the same
            file path as the latest file entry are returned. If revisions with the
            same file id are desired, then mode must be set to
            ``ListRevisionsMode.id``. The ``ListRevisionsMode.id`` mode is useful to
            retrieve revisions for a given file across moves or renames.
            Route attributes:
            scope: files.metadata.read
            :param str path: The path to the file you want to see the revisions of.
            :param mode: Determines the behavior of the API in listing the revisions
            for a given file path or id.
            :type mode: :class:`dropbox.files.ListRevisionsMode`
            :param int limit: The maximum number of revision entries returned.
            :rtype: :class:`dropbox.files.ListRevisionsResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.ListRevisionsError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_list_revisions(path, mode=mode, limit=limit))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_lock_file_batch(
        self,
        entries: str
    ) -> DropboxResponse:
        """Lock the files at the given paths. A locked file will be writable only

        API Endpoint: /2/files/lock_file_batch
        Namespace: files
        Client type: user

        Args:
            entries (str, required): Parameter for files_lock_file_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            by the lock holder. A successful response indicates that the file has
            been locked. Returns a list of the locked file paths and their metadata
            after this operation.
            Route attributes:
            scope: files.content.write
            :param List[:class:`dropbox.files.LockFileArg`] entries: List of
            'entries'. Each 'entry' contains a path of the file which will be
            locked or queried. Duplicate path arguments in the batch are
            considered only once.
            :rtype: :class:`dropbox.files.LockFileBatchResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.LockFileError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_lock_file_batch(entries))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_move(
        self,
        from_path: str,
        to_path: str,
        allow_shared_folder: str = False,
        autorename: str = False,
        allow_ownership_transfer: str = False
    ) -> DropboxResponse:
        """Move a file or folder to a different location in the user's Dropbox. If

        API Endpoint: /2/files/move
        Namespace: files
        Client type: user

        Args:
            from_path (str, required): Parameter for files_move
            to_path (str, required): Parameter for files_move
            allow_shared_folder (str, optional): Parameter for files_move
            autorename (str, optional): Parameter for files_move
            allow_ownership_transfer (str, optional): Parameter for files_move

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            the source path is a folder all its contents will be moved.
            Route attributes:
            scope: files.content.write
            :param bool allow_shared_folder: This flag has no effect.
            :param bool autorename: If there's a conflict, have the Dropbox server
            try to autorename the file to avoid the conflict.
            :param bool allow_ownership_transfer: Allow moves by owner even if it
            would result in an ownership transfer for the content being moved.
            This does not apply to copies.
            :rtype: :class:`dropbox.files.Metadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.RelocationError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_move(from_path, to_path, allow_shared_folder=allow_shared_folder, autorename=autorename, allow_ownership_transfer=allow_ownership_transfer))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_move_batch(
        self,
        entries: str,
        autorename: str = False,
        allow_shared_folder: str = False,
        allow_ownership_transfer: str = False
    ) -> DropboxResponse:
        """Move multiple files or folders to different locations at once in the

        API Endpoint: /2/files/move_batch
        Namespace: files
        Client type: user

        Args:
            entries (str, required): Parameter for files_move_batch
            autorename (str, optional): Parameter for files_move_batch
            allow_shared_folder (str, optional): Parameter for files_move_batch
            allow_ownership_transfer (str, optional): Parameter for files_move_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            user's Dropbox. This route will return job ID immediately and do the
            async moving job in background. Please use
            :meth:`files_move_batch_check` to check the job status.
            Route attributes:
            scope: files.content.write
            :param bool allow_shared_folder: This flag has no effect.
            :param bool allow_ownership_transfer: Allow moves by owner even if it
            would result in an ownership transfer for the content being moved.
            This does not apply to copies.
            :rtype: :class:`dropbox.files.RelocationBatchLaunch`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_move_batch(entries, autorename=autorename, allow_shared_folder=allow_shared_folder, allow_ownership_transfer=allow_ownership_transfer))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_move_batch_check(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Returns the status of an asynchronous job for :meth:`files_move_batch`.

        API Endpoint: /2/files/move_batch_check
        Namespace: files
        Client type: user

        Args:
            async_job_id (str, required): Parameter for files_move_batch_check

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            If success, it returns list of results for each entry.
            Route attributes:
            scope: files.content.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.files.RelocationBatchJobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.PollError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_move_batch_check(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_move_batch_check_v2(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Returns the status of an asynchronous job for

        API Endpoint: /2/files/move_batch_check_v2
        Namespace: files
        Client type: user

        Args:
            async_job_id (str, required): Parameter for files_move_batch_check_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`files_move_batch_v2`. It returns list of results for each entry.
            Route attributes:
            scope: files.content.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.files.RelocationBatchV2JobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.PollError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_move_batch_check_v2(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_move_batch_v2(
        self,
        entries: str,
        autorename: str = False,
        allow_ownership_transfer: str = False
    ) -> DropboxResponse:
        """Move multiple files or folders to different locations at once in the

        API Endpoint: /2/files/move_batch_v2
        Namespace: files
        Client type: user

        Args:
            entries (str, required): Parameter for files_move_batch_v2
            autorename (str, optional): Parameter for files_move_batch_v2
            allow_ownership_transfer (str, optional): Parameter for files_move_batch_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            user's Dropbox. Note that we do not currently support case-only
            renaming. This route will replace :meth:`files_move_batch`. The main
            difference is this route will return status for each entry, while
            :meth:`files_move_batch` raises failure if any entry fails. This route
            will either finish synchronously, or return a job ID and do the async
            move job in background. Please use :meth:`files_move_batch_check_v2` to
            check the job status.
            Route attributes:
            scope: files.content.write
            :param bool allow_ownership_transfer: Allow moves by owner even if it
            would result in an ownership transfer for the content being moved.
            This does not apply to copies.
            :rtype: :class:`dropbox.files.RelocationBatchV2Launch`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_move_batch_v2(entries, autorename=autorename, allow_ownership_transfer=allow_ownership_transfer))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_move_v2(
        self,
        from_path: str,
        to_path: str,
        allow_shared_folder: str = False,
        autorename: str = False,
        allow_ownership_transfer: str = False
    ) -> DropboxResponse:
        """Move a file or folder to a different location in the user's Dropbox. If

        API Endpoint: /2/files/move_v2
        Namespace: files
        Client type: user

        Args:
            from_path (str, required): Parameter for files_move_v2
            to_path (str, required): Parameter for files_move_v2
            allow_shared_folder (str, optional): Parameter for files_move_v2
            autorename (str, optional): Parameter for files_move_v2
            allow_ownership_transfer (str, optional): Parameter for files_move_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            the source path is a folder all its contents will be moved. Note that we
            do not currently support case-only renaming.
            Route attributes:
            scope: files.content.write
            :param bool allow_shared_folder: This flag has no effect.
            :param bool autorename: If there's a conflict, have the Dropbox server
            try to autorename the file to avoid the conflict.
            :param bool allow_ownership_transfer: Allow moves by owner even if it
            would result in an ownership transfer for the content being moved.
            This does not apply to copies.
            :rtype: :class:`dropbox.files.RelocationResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.RelocationError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_move_v2(from_path, to_path, allow_shared_folder=allow_shared_folder, autorename=autorename, allow_ownership_transfer=allow_ownership_transfer))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_paper_create(
        self,
        f: str,
        path: str,
        import_format: str
    ) -> DropboxResponse:
        """Creates a new Paper doc with the provided content.

        API Endpoint: /2/files/paper_create
        Namespace: files
        Client type: user

        Args:
            f (str, required): Parameter for files_paper_create
            path (str, required): Parameter for files_paper_create
            import_format (str, required): Parameter for files_paper_create

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.content.write
            :param bytes f: Contents to upload.
            :param str path: The fully qualified path to the location in the user's
            Dropbox where the Paper Doc should be created. This should include
            the document's title and end with .paper.
            :param import_format: The format of the provided data.
            :type import_format: :class:`dropbox.files.ImportFormat`
            :rtype: :class:`dropbox.files.PaperCreateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.PaperCreateError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_paper_create(f, path, import_format))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_paper_update(
        self,
        f: str,
        path: str,
        import_format: str,
        doc_update_policy: str,
        paper_revision: Optional[str] = None
    ) -> DropboxResponse:
        """Updates an existing Paper doc with the provided content.

        API Endpoint: /2/files/paper_update
        Namespace: files
        Client type: user

        Args:
            f (str, required): Parameter for files_paper_update
            path (str, required): Parameter for files_paper_update
            import_format (str, required): Parameter for files_paper_update
            doc_update_policy (str, required): Parameter for files_paper_update
            paper_revision (str, optional): Parameter for files_paper_update

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.content.write
            :param bytes f: Contents to upload.
            :param str path: Path in the user's Dropbox to update. The path must
            correspond to a Paper doc or an error will be returned.
            :param import_format: The format of the provided data.
            :type import_format: :class:`dropbox.files.ImportFormat`
            :param doc_update_policy: How the provided content should be applied to
            the doc.
            :type doc_update_policy: :class:`dropbox.files.PaperDocUpdatePolicy`
            :param Nullable[int] paper_revision: The latest doc revision. Required
            when doc_update_policy is update. This value must match the current
            revision of the doc or error revision_mismatch will be returned.
            :rtype: :class:`dropbox.files.PaperUpdateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.PaperUpdateError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_paper_update(f, path, import_format, doc_update_policy, paper_revision=paper_revision))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_permanently_delete(
        self,
        path: str,
        parent_rev: Optional[str] = None
    ) -> DropboxResponse:
        """Permanently delete the file or folder at a given path (see

        API Endpoint: /2/files/permanently_delete
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_permanently_delete
            parent_rev (str, optional): Parameter for files_permanently_delete

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            https://www.dropbox.com/en/help/40). If the given file or folder is not
            yet deleted, this route will first delete it. It is possible for this
            route to successfully delete, then fail to permanently delete. Note:
            This endpoint is only available for Dropbox Business apps.
            Route attributes:
            scope: files.permanent_delete
            :param str path: Path in the user's Dropbox to delete.
            :param Nullable[str] parent_rev: Perform delete if given "rev" matches
            the existing file's latest "rev". This field does not support
            deleting a folder.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.DeleteError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_permanently_delete(path, parent_rev=parent_rev))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_properties_add(
        self,
        path: str,
        property_groups: str
    ) -> DropboxResponse:
        """Route attributes:

        API Endpoint: /2/files/properties_add
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_properties_add
            property_groups (str, required): Parameter for files_properties_add

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            scope: files.metadata.write
            :param str path: A unique identifier for the file or folder.
            :param List[:class:`dropbox.files.PropertyGroup`] property_groups: The
            property groups which are to be added to a Dropbox file. No two
            groups in the input should  refer to the same template.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.AddPropertiesError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_properties_add(path, property_groups))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_properties_overwrite(
        self,
        path: str,
        property_groups: str
    ) -> DropboxResponse:
        """Route attributes:

        API Endpoint: /2/files/properties_overwrite
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_properties_overwrite
            property_groups (str, required): Parameter for files_properties_overwrite

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            scope: files.metadata.write
            :param str path: A unique identifier for the file or folder.
            :param List[:class:`dropbox.files.PropertyGroup`] property_groups: The
            property groups "snapshot" updates to force apply. No two groups in
            the input should  refer to the same template.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.InvalidPropertyGroupError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_properties_overwrite(path, property_groups))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_properties_remove(
        self,
        path: str,
        property_template_ids: str
    ) -> DropboxResponse:
        """Route attributes:

        API Endpoint: /2/files/properties_remove
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_properties_remove
            property_template_ids (str, required): Parameter for files_properties_remove

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            scope: files.metadata.write
            :param str path: A unique identifier for the file or folder.
            :param List[str] property_template_ids: A list of identifiers for a
            template created by :meth:`files_templates_add_for_user` or
            :meth:`files_templates_add_for_team`.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.RemovePropertiesError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_properties_remove(path, property_template_ids))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_properties_template_get(
        self,
        template_id: str
    ) -> DropboxResponse:
        """Route attributes:

        API Endpoint: /2/files/properties_template_get
        Namespace: files
        Client type: user

        Args:
            template_id (str, required): Parameter for files_properties_template_get

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            scope: files.metadata.read
            :param str template_id: An identifier for template added by route  See
            :meth:`files_templates_add_for_user` or
            :meth:`files_templates_add_for_team`.
            :rtype: :class:`dropbox.files.GetTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.TemplateError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_properties_template_get(template_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_properties_template_list(self) -> DropboxResponse:
        """Route attributes:

        API Endpoint: /2/files/properties_template_list
        Namespace: files
        Client type: user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            scope: files.metadata.read
            :rtype: :class:`dropbox.files.ListTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.TemplateError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_properties_template_list())
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_properties_update(
        self,
        path: str,
        update_property_groups: str
    ) -> DropboxResponse:
        """Route attributes:

        API Endpoint: /2/files/properties_update
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_properties_update
            update_property_groups (str, required): Parameter for files_properties_update

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            scope: files.metadata.write
            :param str path: A unique identifier for the file or folder.
            :param List[:class:`dropbox.files.PropertyGroupUpdate`]
            update_property_groups: The property groups "delta" updates to
            apply.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.UpdatePropertiesError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_properties_update(path, update_property_groups))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_restore(
        self,
        path: str,
        rev: str
    ) -> DropboxResponse:
        """Restore a specific revision of a file to the given path.

        API Endpoint: /2/files/restore
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_restore
            rev (str, required): Parameter for files_restore

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.content.write
            :param str path: The path to save the restored file.
            :param str rev: The revision to restore.
            :rtype: :class:`dropbox.files.FileMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.RestoreError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_restore(path, rev))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_save_url(
        self,
        path: str,
        url: str
    ) -> DropboxResponse:
        """Save the data from a specified URL into a file in user's Dropbox. Note

        API Endpoint: /2/files/save_url
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_save_url
            url (str, required): Parameter for files_save_url

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            that the transfer from the URL must complete within 15 minutes, or the
            operation will time out and the job will fail. If the given path already
            exists, the file will be renamed to avoid the conflict (e.g. myfile
            (1).txt).
            Route attributes:
            scope: files.content.write
            :param str path: The path in Dropbox where the URL will be saved to.
            :param str url: The URL to be saved.
            :rtype: :class:`dropbox.files.SaveUrlResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.SaveUrlError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_save_url(path, url))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_save_url_check_job_status(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Check the status of a :meth:`files_save_url` job.

        API Endpoint: /2/files/save_url_check_job_status
        Namespace: files
        Client type: user

        Args:
            async_job_id (str, required): Parameter for files_save_url_check_job_status

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.content.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.files.SaveUrlJobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.PollError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_save_url_check_job_status(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_search(
        self,
        path: str,
        query: str,
        start: str = 0,
        max_results: str = 100,
        mode: str = SearchMode('filename', None)
    ) -> DropboxResponse:
        """Searches for files and folders. Note: Recent changes will be reflected

        API Endpoint: /2/files/search
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_search
            query (str, required): Parameter for files_search
            start (str, optional): Parameter for files_search
            max_results (str, optional): Parameter for files_search
            mode (str, optional): Parameter for files_search

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            in search results within a few seconds and older revisions of existing
            files may still match your query for up to a few days.
            Route attributes:
            scope: files.metadata.read
            :param str path: The path in the user's Dropbox to search. Should
            probably be a folder.
            :param str query: The string to search for. Query string may be
            rewritten to improve relevance of results. The string is split on
            spaces into multiple tokens. For file name searching, the last token
            is used for prefix matching (i.e. "bat c" matches "bat cave" but not
            "batman car").
            :param int start: The starting index within the search results (used for
            paging).
            :param int max_results: The maximum number of search results to return.
            :param mode: The search mode (filename, filename_and_content, or
            deleted_filename). Note that searching file content is only
            available for Dropbox Business accounts.
            :type mode: :class:`dropbox.files.SearchMode`
            :rtype: :class:`dropbox.files.SearchResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.SearchError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_search(path, query, start=start, max_results=max_results, mode=mode))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_search_continue_v2(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Fetches the next page of search results returned from

        API Endpoint: /2/files/search_continue_v2
        Namespace: files
        Client type: user

        Args:
            cursor (str, required): Parameter for files_search_continue_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`files_search_v2`. Note: :meth:`files_search_v2` along with
            :meth:`files_search_continue_v2` can only be used to retrieve a maximum
            of 10,000 matches. Recent changes may not immediately be reflected in
            search results due to a short delay in indexing. Duplicate results may
            be returned across pages. Some results may not be returned.
            Route attributes:
            scope: files.metadata.read
            :param str cursor: The cursor returned by your last call to
            :meth:`files_search_v2`. Used to fetch the next page of results.
            :rtype: :class:`dropbox.files.SearchV2Result`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.SearchError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_search_continue_v2(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_search_v2(
        self,
        query: str,
        options: Optional[str] = None,
        match_field_options: Optional[str] = None,
        include_highlights: Optional[str] = None
    ) -> DropboxResponse:
        """Searches for files and folders. Note: :meth:`files_search_v2` along with

        API Endpoint: /2/files/search_v2
        Namespace: files
        Client type: user

        Args:
            query (str, required): Parameter for files_search_v2
            options (str, optional): Parameter for files_search_v2
            match_field_options (str, optional): Parameter for files_search_v2
            include_highlights (str, optional): Parameter for files_search_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`files_search_continue_v2` can only be used to retrieve a maximum
            of 10,000 matches. Recent changes may not immediately be reflected in
            search results due to a short delay in indexing. Duplicate results may
            be returned across pages. Some results may not be returned.
            Route attributes:
            scope: files.metadata.read
            :param str query: The string to search for. May match across multiple
            fields based on the request arguments.
            :param Nullable[:class:`dropbox.files.SearchOptions`] options: Options
            for more targeted search results.
            :param Nullable[:class:`dropbox.files.SearchMatchFieldOptions`]
            match_field_options: Options for search results match fields.
            :param Nullable[bool] include_highlights: Deprecated and moved this
            option to SearchMatchFieldOptions.
            :rtype: :class:`dropbox.files.SearchV2Result`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.SearchError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_search_v2(query, options=options, match_field_options=match_field_options, include_highlights=include_highlights))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_tags_add(
        self,
        path: str,
        tag_text: str
    ) -> DropboxResponse:
        """Add a tag to an item. A tag is a string. The strings are automatically

        API Endpoint: /2/files/tags_add
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_tags_add
            tag_text (str, required): Parameter for files_tags_add

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            converted to lowercase letters. No more than 20 tags can be added to a
            given item.
            Route attributes:
            scope: files.metadata.write
            :param str path: Path to the item to be tagged.
            :param str tag_text: The value of the tag to add. Will be automatically
            converted to lowercase letters.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.AddTagError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_tags_add(path, tag_text))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_tags_get(
        self,
        paths: str
    ) -> DropboxResponse:
        """Get list of tags assigned to items.

        API Endpoint: /2/files/tags_get
        Namespace: files
        Client type: user

        Args:
            paths (str, required): Parameter for files_tags_get

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.metadata.read
            :param List[str] paths: Path to the items.
            :rtype: :class:`dropbox.files.GetTagsResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.BaseTagError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_tags_get(paths))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_tags_remove(
        self,
        path: str,
        tag_text: str
    ) -> DropboxResponse:
        """Remove a tag from an item.

        API Endpoint: /2/files/tags_remove
        Namespace: files
        Client type: user

        Args:
            path (str, required): Parameter for files_tags_remove
            tag_text (str, required): Parameter for files_tags_remove

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.metadata.write
            :param str path: Path to the item to tag.
            :param str tag_text: The tag to remove. Will be automatically converted
            to lowercase letters.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.RemoveTagError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_tags_remove(path, tag_text))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_unlock_file_batch(
        self,
        entries: str
    ) -> DropboxResponse:
        """Unlock the files at the given paths. A locked file can only be unlocked

        API Endpoint: /2/files/unlock_file_batch
        Namespace: files
        Client type: user

        Args:
            entries (str, required): Parameter for files_unlock_file_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            by the lock holder or, if a business account, a team admin. A successful
            response indicates that the file has been unlocked. Returns a list of
            the unlocked file paths and their metadata after this operation.
            Route attributes:
            scope: files.content.write
            :param List[:class:`dropbox.files.UnlockFileArg`] entries: List of
            'entries'. Each 'entry' contains a path of the file which will be
            unlocked. Duplicate path arguments in the batch are considered only
            once.
            :rtype: :class:`dropbox.files.LockFileBatchResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.LockFileError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_unlock_file_batch(entries))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_upload(
        self,
        f: bytes,
        path: str,
        mode: str = WriteMode('add', None),
        autorename: str = False,
        client_modified: Optional[str] = None,
        mute: str = False,
        property_groups: Optional[str] = None,
        strict_conflict: str = False,
        content_hash: Optional[str] = None
    ) -> DropboxResponse:
        """Create a new file with the contents provided in the request. You can use ;)

        API Endpoint: /2/files/upload
        Namespace: files
        Client type: user

        Args:
            f (bytes, required): Parameter for files_upload
            path (str, required): Parameter for files_upload
            mode (str, optional): Parameter for files_upload
            autorename (str, optional): Parameter for files_upload
            client_modified (str, optional): Parameter for files_upload
            mute (str, optional): Parameter for files_upload
            property_groups (str, optional): Parameter for files_upload
            strict_conflict (str, optional): Parameter for files_upload
            content_hash (str, optional): Parameter for files_upload

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to upload a file larger than 150 MB. Instead, create an upload
            session with :meth:`files_upload_session_start`. Calls to this endpoint
            will count as data transport calls for any Dropbox Business teams with a
            limit on the number of data transport calls allowed per month. For more
            information, see the `Data transport limit page
            <https://www.dropbox.com/developers/reference/data-transport-limit>`_.
            Route attributes:
            scope: files.content.write
            :param bytes f: Contents to upload.
            :param Nullable[str] content_hash: A hash of the file content uploaded
            in this call. If provided and the uploaded content does not match
            this hash, an error will be returned. For more information see our
            `Content hash
            <https://www.dropbox.com/developers/reference/content-hash>`_ page.
            :rtype: :class:`dropbox.files.FileMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.UploadError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_upload(f, path, mode=mode, autorename=autorename, client_modified=client_modified, mute=mute, property_groups=property_groups, strict_conflict=strict_conflict, content_hash=content_hash))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_upload_session_append(
        self,
        f: str,
        session_id: str,
        offset: str
    ) -> DropboxResponse:
        """Append more data to an upload session. A single request should not

        API Endpoint: /2/files/upload_session_append
        Namespace: files
        Client type: user

        Args:
            f (str, required): Parameter for files_upload_session_append
            session_id (str, required): Parameter for files_upload_session_append
            offset (str, required): Parameter for files_upload_session_append

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            upload more than 150 MB. The maximum size of a file one can upload to an
            upload session is 350 GB. Calls to this endpoint will count as data
            transport calls for any Dropbox Business teams with a limit on the
            number of data transport calls allowed per month. For more information,
            see the `Data transport limit page
            <https://www.dropbox.com/developers/reference/data-transport-limit>`_.
            Route attributes:
            scope: files.content.write
            :param bytes f: Contents to upload.
            :param str session_id: The upload session ID (returned by
            :meth:`files_upload_session_start`).
            :param int offset: Offset in bytes at which data should be appended. We
            use this to make sure upload data isn't lost or duplicated in the
            event of a network error.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.UploadSessionAppendError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_upload_session_append(f, session_id, offset))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_upload_session_append_v2(
        self,
        f: str,
        cursor: str,
        close: str = False,
        content_hash: Optional[str] = None
    ) -> DropboxResponse:
        """Append more data to an upload session. When the parameter close is set,

        API Endpoint: /2/files/upload_session_append_v2
        Namespace: files
        Client type: user

        Args:
            f (str, required): Parameter for files_upload_session_append_v2
            cursor (str, required): Parameter for files_upload_session_append_v2
            close (str, optional): Parameter for files_upload_session_append_v2
            content_hash (str, optional): Parameter for files_upload_session_append_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this call will close the session. A single request should not upload
            more than 150 MB. The maximum size of a file one can upload to an upload
            session is 350 GB. Calls to this endpoint will count as data transport
            calls for any Dropbox Business teams with a limit on the number of data
            transport calls allowed per month. For more information, see the `Data
            transport limit page
            <https://www.dropbox.com/developers/reference/data-transport-limit>`_.
            Route attributes:
            scope: files.content.write
            :param bytes f: Contents to upload.
            :param cursor: Contains the upload session ID and the offset.
            :type cursor: :class:`dropbox.files.UploadSessionCursor`
            :param bool close: If true, the current session will be closed, at which
            point you won't be able to call
            :meth:`files_upload_session_append_v2` anymore with the current
            session.
            :param Nullable[str] content_hash: A hash of the file content uploaded
            in this call. If provided and the uploaded content does not match
            this hash, an error will be returned. For more information see our
            `Content hash
            <https://www.dropbox.com/developers/reference/content-hash>`_ page.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.UploadSessionAppendError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_upload_session_append_v2(f, cursor, close=close, content_hash=content_hash))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_upload_session_finish(
        self,
        f: str,
        cursor: str,
        commit: str,
        content_hash: Optional[str] = None
    ) -> DropboxResponse:
        """Finish an upload session and save the uploaded data to the given file

        API Endpoint: /2/files/upload_session_finish
        Namespace: files
        Client type: user

        Args:
            f (str, required): Parameter for files_upload_session_finish
            cursor (str, required): Parameter for files_upload_session_finish
            commit (str, required): Parameter for files_upload_session_finish
            content_hash (str, optional): Parameter for files_upload_session_finish

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            path. A single request should not upload more than 150 MB. The maximum
            size of a file one can upload to an upload session is 350 GB. Calls to
            this endpoint will count as data transport calls for any Dropbox
            Business teams with a limit on the number of data transport calls
            allowed per month. For more information, see the `Data transport limit
            page
            <https://www.dropbox.com/developers/reference/data-transport-limit>`_.
            Route attributes:
            scope: files.content.write
            :param bytes f: Contents to upload.
            :param cursor: Contains the upload session ID and the offset.
            :type cursor: :class:`dropbox.files.UploadSessionCursor`
            :param commit: Contains the path and other optional modifiers for the
            commit.
            :type commit: :class:`dropbox.files.CommitInfo`
            :param Nullable[str] content_hash: A hash of the file content uploaded
            in this call. If provided and the uploaded content does not match
            this hash, an error will be returned. For more information see our
            `Content hash
            <https://www.dropbox.com/developers/reference/content-hash>`_ page.
            :rtype: :class:`dropbox.files.FileMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.UploadSessionFinishError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_upload_session_finish(f, cursor, commit, content_hash=content_hash))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_upload_session_finish_batch(
        self,
        entries: str
    ) -> DropboxResponse:
        """This route helps you commit many files at once into a user's Dropbox.

        API Endpoint: /2/files/upload_session_finish_batch
        Namespace: files
        Client type: user

        Args:
            entries (str, required): Parameter for files_upload_session_finish_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Use :meth:`files_upload_session_start` and
            :meth:`files_upload_session_append_v2` to upload file contents. We
            recommend uploading many files in parallel to increase throughput. Once
            the file contents have been uploaded, rather than calling
            :meth:`files_upload_session_finish`, use this route to finish all your
            upload sessions in a single request. ``UploadSessionStartArg.close`` or
            ``UploadSessionAppendArg.close`` needs to be true for the last
            :meth:`files_upload_session_start` or
            :meth:`files_upload_session_append_v2` call. The maximum size of a file
            one can upload to an upload session is 350 GB. This route will return a
            job_id immediately and do the async commit job in background. Use
            :meth:`files_upload_session_finish_batch_check` to check the job status.
            For the same account, this route should be executed serially. That means
            you should not start the next job before current job finishes. We allow
            up to 1000 entries in a single request. Calls to this endpoint will
            count as data transport calls for any Dropbox Business teams with a
            limit on the number of data transport calls allowed per month. For more
            information, see the `Data transport limit page
            <https://www.dropbox.com/developers/reference/data-transport-limit>`_.
            Route attributes:
            scope: files.content.write
            :param List[:class:`dropbox.files.UploadSessionFinishArg`] entries:
            Commit information for each file in the batch.
            :rtype: :class:`dropbox.files.UploadSessionFinishBatchLaunch`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_upload_session_finish_batch(entries))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_upload_session_finish_batch_check(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Returns the status of an asynchronous job for

        API Endpoint: /2/files/upload_session_finish_batch_check
        Namespace: files
        Client type: user

        Args:
            async_job_id (str, required): Parameter for files_upload_session_finish_batch_check

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`files_upload_session_finish_batch`. If success, it returns list
            of result for each entry.
            Route attributes:
            scope: files.content.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.files.UploadSessionFinishBatchJobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.PollError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_upload_session_finish_batch_check(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_upload_session_finish_batch_v2(
        self,
        entries: str
    ) -> DropboxResponse:
        """This route helps you commit many files at once into a user's Dropbox.

        API Endpoint: /2/files/upload_session_finish_batch_v2
        Namespace: files
        Client type: user

        Args:
            entries (str, required): Parameter for files_upload_session_finish_batch_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Use :meth:`files_upload_session_start` and
            :meth:`files_upload_session_append_v2` to upload file contents. We
            recommend uploading many files in parallel to increase throughput. Once
            the file contents have been uploaded, rather than calling
            :meth:`files_upload_session_finish`, use this route to finish all your
            upload sessions in a single request. ``UploadSessionStartArg.close`` or
            ``UploadSessionAppendArg.close`` needs to be true for the last
            :meth:`files_upload_session_start` or
            :meth:`files_upload_session_append_v2` call of each upload session. The
            maximum size of a file one can upload to an upload session is 350 GB. We
            allow up to 1000 entries in a single request. Calls to this endpoint
            will count as data transport calls for any Dropbox Business teams with a
            limit on the number of data transport calls allowed per month. For more
            information, see the `Data transport limit page
            <https://www.dropbox.com/developers/reference/data-transport-limit>`_.
            Route attributes:
            scope: files.content.write
            :param List[:class:`dropbox.files.UploadSessionFinishArg`] entries:
            Commit information for each file in the batch.
            :rtype: :class:`dropbox.files.UploadSessionFinishBatchResult`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_upload_session_finish_batch_v2(entries))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_upload_session_start(
        self,
        f: str,
        close: str = False,
        session_type: Optional[str] = None,
        content_hash: Optional[str] = None
    ) -> DropboxResponse:
        """Upload sessions allow you to upload a single file in one or more

        API Endpoint: /2/files/upload_session_start
        Namespace: files
        Client type: user

        Args:
            f (str, required): Parameter for files_upload_session_start
            close (str, optional): Parameter for files_upload_session_start
            session_type (str, optional): Parameter for files_upload_session_start
            content_hash (str, optional): Parameter for files_upload_session_start

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            requests, for example where the size of the file is greater than 150 MB.
            This call starts a new upload session with the given data. You can then
            use :meth:`files_upload_session_append_v2` to add more data and
            :meth:`files_upload_session_finish` to save all the data to a file in
            Dropbox. A single request should not upload more than 150 MB. The
            maximum size of a file one can upload to an upload session is 350 GB. An
            upload session can be used for a maximum of 7 days. Attempting to use an
            ``UploadSessionStartResult.session_id`` with
            :meth:`files_upload_session_append_v2` or
            :meth:`files_upload_session_finish` more than 7 days after its creation
            will return a ``UploadSessionLookupError.not_found``. Calls to this
            endpoint will count as data transport calls for any Dropbox Business
            teams with a limit on the number of data transport calls allowed per
            month. For more information, see the `Data transport limit page
            <https://www.dropbox.com/developers/reference/data-transport-limit>`_.
            By default, upload sessions require you to send content of the file in
            sequential order via consecutive :meth:`files_upload_session_start`,
            :meth:`files_upload_session_append_v2`,
            :meth:`files_upload_session_finish` calls. For better performance, you
            can instead optionally use a ``UploadSessionType.concurrent`` upload
            session. To start a new concurrent session, set
            ``UploadSessionStartArg.session_type`` to
            ``UploadSessionType.concurrent``. After that, you can send file data in
            concurrent :meth:`files_upload_session_append_v2` requests. Finally
            finish the session with :meth:`files_upload_session_finish`. There are
            couple of constraints with concurrent sessions to make them work. You
            can not send data with :meth:`files_upload_session_start` or
            :meth:`files_upload_session_finish` call, only with
            :meth:`files_upload_session_append_v2` call. Also data uploaded in
            :meth:`files_upload_session_append_v2` call must be multiple of 4194304
            bytes (except for last :meth:`files_upload_session_append_v2` with
            ``UploadSessionStartArg.close`` to ``True``, that may contain any
            remaining data).
            Route attributes:
            scope: files.content.write
            :param bytes f: Contents to upload.
            :param bool close: If true, the current session will be closed, at which
            point you won't be able to call
            :meth:`files_upload_session_append_v2` anymore with the current
            session.
            :param Nullable[:class:`dropbox.files.UploadSessionType`] session_type:
            Type of upload session you want to start. If not specified, default
            is ``UploadSessionType.sequential``.
            :param Nullable[str] content_hash: A hash of the file content uploaded
            in this call. If provided and the uploaded content does not match
            this hash, an error will be returned. For more information see our
            `Content hash
            <https://www.dropbox.com/developers/reference/content-hash>`_ page.
            :rtype: :class:`dropbox.files.UploadSessionStartResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.files.UploadSessionStartError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_upload_session_start(f, close=close, session_type=session_type, content_hash=content_hash))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def files_upload_session_start_batch(
        self,
        num_sessions: str,
        session_type: Optional[str] = None
    ) -> DropboxResponse:
        """This route starts batch of upload_sessions. Please refer to

        API Endpoint: /2/files/upload_session_start_batch
        Namespace: files
        Client type: user

        Args:
            num_sessions (str, required): Parameter for files_upload_session_start_batch
            session_type (str, optional): Parameter for files_upload_session_start_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            `upload_session/start` usage. Calls to this endpoint will count as data
            transport calls for any Dropbox Business teams with a limit on the
            number of data transport calls allowed per month. For more information,
            see the `Data transport limit page
            <https://www.dropbox.com/developers/reference/data-transport-limit>`_.
            Route attributes:
            scope: files.content.write
            :param Nullable[:class:`dropbox.files.UploadSessionType`] session_type:
            Type of upload session you want to start. If not specified, default
            is ``UploadSessionType.sequential``.
            :param int num_sessions: The number of upload sessions to start.
            :rtype: :class:`dropbox.files.UploadSessionStartBatchResult`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.files_upload_session_start_batch(num_sessions, session_type=session_type))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_archive(
        self,
        doc_id: str
    ) -> DropboxResponse:
        """Marks the given Paper doc as archived. This action can be performed or

        API Endpoint: /2/paper/docs_archive
        Namespace: paper
        Client type: user

        Args:
            doc_id (str, required): Parameter for paper_docs_archive

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            undone by anyone with edit permissions to the doc. Note that this
            endpoint will continue to work for content created by users on the older
            version of Paper. To check which version of Paper a user is on, use
            /users/features/get_values. If the paper_as_files feature is enabled,
            then the user is running the new version of Paper. This endpoint will be
            retired in September 2020. Refer to the `Paper Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for more information.
            Route attributes:
            scope: files.content.write
            :param str doc_id: The Paper doc ID.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.DocLookupError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_archive(doc_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_create(
        self,
        f: str,
        import_format: str,
        parent_folder_id: Optional[str] = None
    ) -> DropboxResponse:
        """Creates a new Paper doc with the provided content. Note that this

        API Endpoint: /2/paper/docs_create
        Namespace: paper
        Client type: user

        Args:
            f (str, required): Parameter for paper_docs_create
            import_format (str, required): Parameter for paper_docs_create
            parent_folder_id (str, optional): Parameter for paper_docs_create

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            endpoint will continue to work for content created by users on the older
            version of Paper. To check which version of Paper a user is on, use
            /users/features/get_values. If the paper_as_files feature is enabled,
            then the user is running the new version of Paper. This endpoint will be
            retired in September 2020. Refer to the `Paper Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for more information.
            Route attributes:
            scope: files.content.write
            :param bytes f: Contents to upload.
            :param Nullable[str] parent_folder_id: The Paper folder ID where the
            Paper document should be created. The API user has to have write
            access to this folder or error is thrown.
            :param import_format: The format of provided data.
            :type import_format: :class:`dropbox.paper.ImportFormat`
            :rtype: :class:`dropbox.paper.PaperDocCreateUpdateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.PaperDocCreateError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_create(f, import_format, parent_folder_id=parent_folder_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_download(
        self,
        doc_id: str,
        export_format: str
    ) -> DropboxResponse:
        """Exports and downloads Paper doc either as HTML or markdown. Note that

        API Endpoint: /2/paper/docs_download
        Namespace: paper
        Client type: user

        Args:
            doc_id (str, required): Parameter for paper_docs_download
            export_format (str, required): Parameter for paper_docs_download

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this endpoint will continue to work for content created by users on the
            older version of Paper. To check which version of Paper a user is on,
            use /users/features/get_values. If the paper_as_files feature is
            enabled, then the user is running the new version of Paper. Refer to the
            `Paper Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: files.content.read
            :type export_format: :class:`dropbox.paper.ExportFormat`
            :rtype: (:class:`dropbox.paper.PaperDocExportResult`,
            :class:`requests.models.Response`)
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.DocLookupError`
            If you do not consume the entire response body, then you must call close
            on the response object, otherwise you will max out your available
            connections. We recommend using the `contextlib.closing
            <https://docs.python.org/2/library/contextlib.html#contextlib.closing>`_
            context manager to ensure this.
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_download(doc_id, export_format))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_download_to_file(
        self,
        download_path: str,
        doc_id: str,
        export_format: str
    ) -> DropboxResponse:
        """Exports and downloads Paper doc either as HTML or markdown. Note that

        API Endpoint: /2/paper/docs_download_to_file
        Namespace: paper
        Client type: user

        Args:
            download_path (str, required): Parameter for paper_docs_download_to_file
            doc_id (str, required): Parameter for paper_docs_download_to_file
            export_format (str, required): Parameter for paper_docs_download_to_file

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this endpoint will continue to work for content created by users on the
            older version of Paper. To check which version of Paper a user is on,
            use /users/features/get_values. If the paper_as_files feature is
            enabled, then the user is running the new version of Paper. Refer to the
            `Paper Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: files.content.read
            :param str download_path: Path on local machine to save file.
            :type export_format: :class:`dropbox.paper.ExportFormat`
            :rtype: :class:`dropbox.paper.PaperDocExportResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.DocLookupError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_download_to_file(download_path, doc_id, export_format))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_folder_users_list(
        self,
        doc_id: str,
        limit: str = 1000
    ) -> DropboxResponse:
        """Lists the users who are explicitly invited to the Paper folder in which

        API Endpoint: /2/paper/docs_folder_users_list
        Namespace: paper
        Client type: user

        Args:
            doc_id (str, required): Parameter for paper_docs_folder_users_list
            limit (str, optional): Parameter for paper_docs_folder_users_list

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            the Paper doc is contained. For private folders all users (including
            owner) shared on the folder are listed and for team folders all non-team
            users shared on the folder are returned. Note that this endpoint will
            continue to work for content created by users on the older version of
            Paper. To check which version of Paper a user is on, use
            /users/features/get_values. If the paper_as_files feature is enabled,
            then the user is running the new version of Paper. Refer to the `Paper
            Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: sharing.read
            :param int limit: Size limit per batch. The maximum number of users that
            can be retrieved per batch is 1000. Higher value results in invalid
            arguments error.
            :rtype: :class:`dropbox.paper.ListUsersOnFolderResponse`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.DocLookupError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_folder_users_list(doc_id, limit=limit))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_folder_users_list_continue(
        self,
        doc_id: str,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from

        API Endpoint: /2/paper/docs_folder_users_list_continue
        Namespace: paper
        Client type: user

        Args:
            doc_id (str, required): Parameter for paper_docs_folder_users_list_continue
            cursor (str, required): Parameter for paper_docs_folder_users_list_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`paper_docs_folder_users_list`, use this to paginate through all
            users on the Paper folder. Note that this endpoint will continue to work
            for content created by users on the older version of Paper. To check
            which version of Paper a user is on, use /users/features/get_values. If
            the paper_as_files feature is enabled, then the user is running the new
            version of Paper. Refer to the `Paper Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: sharing.read
            :param str cursor: The cursor obtained from
            :meth:`paper_docs_folder_users_list` or
            :meth:`paper_docs_folder_users_list_continue`. Allows for
            pagination.
            :rtype: :class:`dropbox.paper.ListUsersOnFolderResponse`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.ListUsersCursorError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_folder_users_list_continue(doc_id, cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_get_folder_info(
        self,
        doc_id: str
    ) -> DropboxResponse:
        """Retrieves folder information for the given Paper doc. This includes:   -

        API Endpoint: /2/paper/docs_get_folder_info
        Namespace: paper
        Client type: user

        Args:
            doc_id (str, required): Parameter for paper_docs_get_folder_info

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            folder sharing policy; permissions for subfolders are set by the
            top-level folder.   - full 'filepath', i.e. the list of folders (both
            folderId and folderName) from     the root folder to the folder directly
            containing the Paper doc.  If the Paper doc is not in any folder (aka
            unfiled) the response will be empty. Note that this endpoint will
            continue to work for content created by users on the older version of
            Paper. To check which version of Paper a user is on, use
            /users/features/get_values. If the paper_as_files feature is enabled,
            then the user is running the new version of Paper. Refer to the `Paper
            Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: sharing.read
            :param str doc_id: The Paper doc ID.
            :rtype: :class:`dropbox.paper.FoldersContainingPaperDoc`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.DocLookupError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_get_folder_info(doc_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_list(
        self,
        filter_by: str = ListPaperDocsFilterBy('docs_accessed', None),
        sort_by: str = ListPaperDocsSortBy('accessed', None),
        sort_order: str = ListPaperDocsSortOrder('ascending', None),
        limit: str = 1000
    ) -> DropboxResponse:
        """Return the list of all Paper docs according to the argument

        API Endpoint: /2/paper/docs_list
        Namespace: paper
        Client type: user

        Args:
            filter_by (str, optional): Parameter for paper_docs_list
            sort_by (str, optional): Parameter for paper_docs_list
            sort_order (str, optional): Parameter for paper_docs_list
            limit (str, optional): Parameter for paper_docs_list

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            specifications. To iterate over through the full pagination, pass the
            cursor to :meth:`paper_docs_list_continue`. Note that this endpoint will
            continue to work for content created by users on the older version of
            Paper. To check which version of Paper a user is on, use
            /users/features/get_values. If the paper_as_files feature is enabled,
            then the user is running the new version of Paper. Refer to the `Paper
            Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: files.metadata.read
            :param filter_by: Allows user to specify how the Paper docs should be
            filtered.
            :type filter_by: :class:`dropbox.paper.ListPaperDocsFilterBy`
            :param sort_by: Allows user to specify how the Paper docs should be
            sorted.
            :type sort_by: :class:`dropbox.paper.ListPaperDocsSortBy`
            :param sort_order: Allows user to specify the sort order of the result.
            :type sort_order: :class:`dropbox.paper.ListPaperDocsSortOrder`
            :param int limit: Size limit per batch. The maximum number of docs that
            can be retrieved per batch is 1000. Higher value results in invalid
            arguments error.
            :rtype: :class:`dropbox.paper.ListPaperDocsResponse`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_list(filter_by=filter_by, sort_by=sort_by, sort_order=sort_order, limit=limit))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_list_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from :meth:`paper_docs_list`, use this

        API Endpoint: /2/paper/docs_list_continue
        Namespace: paper
        Client type: user

        Args:
            cursor (str, required): Parameter for paper_docs_list_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            to paginate through all Paper doc. Note that this endpoint will continue
            to work for content created by users on the older version of Paper. To
            check which version of Paper a user is on, use
            /users/features/get_values. If the paper_as_files feature is enabled,
            then the user is running the new version of Paper. Refer to the `Paper
            Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: files.metadata.read
            :param str cursor: The cursor obtained from :meth:`paper_docs_list` or
            :meth:`paper_docs_list_continue`. Allows for pagination.
            :rtype: :class:`dropbox.paper.ListPaperDocsResponse`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.ListDocsCursorError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_list_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_permanently_delete(
        self,
        doc_id: str
    ) -> DropboxResponse:
        """Permanently deletes the given Paper doc. This operation is final as the

        API Endpoint: /2/paper/docs_permanently_delete
        Namespace: paper
        Client type: user

        Args:
            doc_id (str, required): Parameter for paper_docs_permanently_delete

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            doc cannot be recovered. This action can be performed only by the doc
            owner. Note that this endpoint will continue to work for content created
            by users on the older version of Paper. To check which version of Paper
            a user is on, use /users/features/get_values. If the paper_as_files
            feature is enabled, then the user is running the new version of Paper.
            Refer to the `Paper Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: files.permanent_delete
            :param str doc_id: The Paper doc ID.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.DocLookupError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_permanently_delete(doc_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_sharing_policy_get(
        self,
        doc_id: str
    ) -> DropboxResponse:
        """Gets the default sharing policy for the given Paper doc. Note that this

        API Endpoint: /2/paper/docs_sharing_policy_get
        Namespace: paper
        Client type: user

        Args:
            doc_id (str, required): Parameter for paper_docs_sharing_policy_get

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            endpoint will continue to work for content created by users on the older
            version of Paper. To check which version of Paper a user is on, use
            /users/features/get_values. If the paper_as_files feature is enabled,
            then the user is running the new version of Paper. Refer to the `Paper
            Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: sharing.read
            :param str doc_id: The Paper doc ID.
            :rtype: :class:`dropbox.paper.SharingPolicy`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.DocLookupError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_sharing_policy_get(doc_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_sharing_policy_set(
        self,
        doc_id: str,
        sharing_policy: str
    ) -> DropboxResponse:
        """Sets the default sharing policy for the given Paper doc. The default

        API Endpoint: /2/paper/docs_sharing_policy_set
        Namespace: paper
        Client type: user

        Args:
            doc_id (str, required): Parameter for paper_docs_sharing_policy_set
            sharing_policy (str, required): Parameter for paper_docs_sharing_policy_set

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            'team_sharing_policy' can be changed only by teams, omit this field for
            personal accounts. The 'public_sharing_policy' policy can't be set to
            the value 'disabled' because this setting can be changed only via the
            team admin console. Note that this endpoint will continue to work for
            content created by users on the older version of Paper. To check which
            version of Paper a user is on, use /users/features/get_values. If the
            paper_as_files feature is enabled, then the user is running the new
            version of Paper. Refer to the `Paper Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: sharing.write
            :param sharing_policy: The default sharing policy to be set for the
            Paper doc.
            :type sharing_policy: :class:`dropbox.paper.SharingPolicy`
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.DocLookupError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_sharing_policy_set(doc_id, sharing_policy))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_update(
        self,
        f: str,
        doc_id: str,
        doc_update_policy: str,
        revision: str,
        import_format: str
    ) -> DropboxResponse:
        """Updates an existing Paper doc with the provided content. Note that this

        API Endpoint: /2/paper/docs_update
        Namespace: paper
        Client type: user

        Args:
            f (str, required): Parameter for paper_docs_update
            doc_id (str, required): Parameter for paper_docs_update
            doc_update_policy (str, required): Parameter for paper_docs_update
            revision (str, required): Parameter for paper_docs_update
            import_format (str, required): Parameter for paper_docs_update

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            endpoint will continue to work for content created by users on the older
            version of Paper. To check which version of Paper a user is on, use
            /users/features/get_values. If the paper_as_files feature is enabled,
            then the user is running the new version of Paper. This endpoint will be
            retired in September 2020. Refer to the `Paper Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for more information.
            Route attributes:
            scope: files.content.write
            :param bytes f: Contents to upload.
            :param doc_update_policy: The policy used for the current update call.
            :type doc_update_policy: :class:`dropbox.paper.PaperDocUpdatePolicy`
            :param int revision: The latest doc revision. This value must match the
            head revision or an error code will be returned. This is to prevent
            colliding writes.
            :param import_format: The format of provided data.
            :type import_format: :class:`dropbox.paper.ImportFormat`
            :rtype: :class:`dropbox.paper.PaperDocCreateUpdateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.PaperDocUpdateError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_update(f, doc_id, doc_update_policy, revision, import_format))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_users_add(
        self,
        doc_id: str,
        members: str,
        custom_message: Optional[str] = None,
        quiet: str = False
    ) -> DropboxResponse:
        """Allows an owner or editor to add users to a Paper doc or change their

        API Endpoint: /2/paper/docs_users_add
        Namespace: paper
        Client type: user

        Args:
            doc_id (str, required): Parameter for paper_docs_users_add
            members (str, required): Parameter for paper_docs_users_add
            custom_message (str, optional): Parameter for paper_docs_users_add
            quiet (str, optional): Parameter for paper_docs_users_add

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            permissions using their email address or Dropbox account ID. The doc
            owner's permissions cannot be changed. Note that this endpoint will
            continue to work for content created by users on the older version of
            Paper. To check which version of Paper a user is on, use
            /users/features/get_values. If the paper_as_files feature is enabled,
            then the user is running the new version of Paper. Refer to the `Paper
            Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: sharing.write
            :param List[:class:`dropbox.paper.AddMember`] members: User which should
            be added to the Paper doc. Specify only email address or Dropbox
            account ID.
            :param Nullable[str] custom_message: A personal message that will be
            emailed to each successfully added member.
            :param bool quiet: Clients should set this to true if no email message
            shall be sent to added users.
            :rtype: List[:class:`dropbox.paper.AddPaperDocUserMemberResult`]
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.DocLookupError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_users_add(doc_id, members, custom_message=custom_message, quiet=quiet))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_users_list(
        self,
        doc_id: str,
        limit: str = 1000,
        filter_by: str = UserOnPaperDocFilter('shared', None)
    ) -> DropboxResponse:
        """Lists all users who visited the Paper doc or users with explicit access.

        API Endpoint: /2/paper/docs_users_list
        Namespace: paper
        Client type: user

        Args:
            doc_id (str, required): Parameter for paper_docs_users_list
            limit (str, optional): Parameter for paper_docs_users_list
            filter_by (str, optional): Parameter for paper_docs_users_list

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            This call excludes users who have been removed. The list is sorted by
            the date of the visit or the share date. The list will include both
            users, the explicitly shared ones as well as those who came in using the
            Paper url link. Note that this endpoint will continue to work for
            content created by users on the older version of Paper. To check which
            version of Paper a user is on, use /users/features/get_values. If the
            paper_as_files feature is enabled, then the user is running the new
            version of Paper. Refer to the `Paper Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: sharing.read
            :param int limit: Size limit per batch. The maximum number of users that
            can be retrieved per batch is 1000. Higher value results in invalid
            arguments error.
            :param filter_by: Specify this attribute if you want to obtain users
            that have already accessed the Paper doc.
            :type filter_by: :class:`dropbox.paper.UserOnPaperDocFilter`
            :rtype: :class:`dropbox.paper.ListUsersOnPaperDocResponse`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.DocLookupError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_users_list(doc_id, limit=limit, filter_by=filter_by))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_users_list_continue(
        self,
        doc_id: str,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from :meth:`paper_docs_users_list`, use

        API Endpoint: /2/paper/docs_users_list_continue
        Namespace: paper
        Client type: user

        Args:
            doc_id (str, required): Parameter for paper_docs_users_list_continue
            cursor (str, required): Parameter for paper_docs_users_list_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to paginate through all users on the Paper doc. Note that this
            endpoint will continue to work for content created by users on the older
            version of Paper. To check which version of Paper a user is on, use
            /users/features/get_values. If the paper_as_files feature is enabled,
            then the user is running the new version of Paper. Refer to the `Paper
            Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: sharing.read
            :param str cursor: The cursor obtained from
            :meth:`paper_docs_users_list` or
            :meth:`paper_docs_users_list_continue`. Allows for pagination.
            :rtype: :class:`dropbox.paper.ListUsersOnPaperDocResponse`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.ListUsersCursorError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_users_list_continue(doc_id, cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_docs_users_remove(
        self,
        doc_id: str,
        member: str
    ) -> DropboxResponse:
        """Allows an owner or editor to remove users from a Paper doc using their

        API Endpoint: /2/paper/docs_users_remove
        Namespace: paper
        Client type: user

        Args:
            doc_id (str, required): Parameter for paper_docs_users_remove
            member (str, required): Parameter for paper_docs_users_remove

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            email address or Dropbox account ID. The doc owner cannot be removed.
            Note that this endpoint will continue to work for content created by
            users on the older version of Paper. To check which version of Paper a
            user is on, use /users/features/get_values. If the paper_as_files
            feature is enabled, then the user is running the new version of Paper.
            Refer to the `Paper Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: sharing.write
            :param member: User which should be removed from the Paper doc. Specify
            only email address or Dropbox account ID.
            :type member: :class:`dropbox.paper.MemberSelector`
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.DocLookupError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_docs_users_remove(doc_id, member))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def paper_folders_create(
        self,
        name: str,
        parent_folder_id: Optional[str] = None,
        is_team_folder: Optional[str] = None
    ) -> DropboxResponse:
        """Create a new Paper folder with the provided info. Note that this

        API Endpoint: /2/paper/folders_create
        Namespace: paper
        Client type: user

        Args:
            name (str, required): Parameter for paper_folders_create
            parent_folder_id (str, optional): Parameter for paper_folders_create
            is_team_folder (str, optional): Parameter for paper_folders_create

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            endpoint will continue to work for content created by users on the older
            version of Paper. To check which version of Paper a user is on, use
            /users/features/get_values. If the paper_as_files feature is enabled,
            then the user is running the new version of Paper. Refer to the `Paper
            Migration Guide
            <https://www.dropbox.com/lp/developers/reference/paper-migration-guide>`_
            for migration information.
            Route attributes:
            scope: files.content.write
            :param str name: The name of the new Paper folder.
            :param Nullable[str] parent_folder_id: The encrypted Paper folder Id
            where the new Paper folder should be created. The API user has to
            have write access to this folder or error is thrown. If not
            supplied, the new folder will be created at top level.
            :param Nullable[bool] is_team_folder: Whether the folder to be created
            should be a team folder. This value will be ignored if
            parent_folder_id is supplied, as the new folder will inherit the
            type (private or team folder) from its parent. We will by default
            create a top-level private folder if both parent_folder_id and
            is_team_folder are not supplied.
            :rtype: :class:`dropbox.paper.PaperFolderCreateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.paper.PaperFolderCreateError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.paper_folders_create(name, parent_folder_id=parent_folder_id, is_team_folder=is_team_folder))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_add_file_member(
        self,
        file: str,
        members: str,
        custom_message: Optional[str] = None,
        quiet: str = False,
        access_level: str = AccessLevel('viewer', None),
        add_message_as_comment: str = False
    ) -> DropboxResponse:
        """Adds specified members to a file.

        API Endpoint: /2/sharing/add_file_member
        Namespace: sharing
        Client type: user

        Args:
            file (str, required): Parameter for sharing_add_file_member
            members (str, required): Parameter for sharing_add_file_member
            custom_message (str, optional): Parameter for sharing_add_file_member
            quiet (str, optional): Parameter for sharing_add_file_member
            access_level (str, optional): Parameter for sharing_add_file_member
            add_message_as_comment (str, optional): Parameter for sharing_add_file_member

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.write
            :param str file: File to which to add members.
            :param List[:class:`dropbox.sharing.MemberSelector`] members: Members to
            add. Note that even an email address is given, this may result in a
            user being directly added to the membership if that email is the
            user's main account email.
            :param Nullable[str] custom_message: Message to send to added members in
            their invitation.
            :param bool quiet: Whether added members should be notified via email
            and device notifications of their invitation.
            :param access_level: AccessLevel union object, describing what access
            level we want to give new members.
            :type access_level: :class:`dropbox.sharing.AccessLevel`
            :param bool add_message_as_comment: If the custom message should be
            added as a comment on the file.
            :rtype: List[:class:`dropbox.sharing.FileMemberActionResult`]
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.AddFileMemberError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_add_file_member(file, members, custom_message=custom_message, quiet=quiet, access_level=access_level, add_message_as_comment=add_message_as_comment))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_add_folder_member(
        self,
        shared_folder_id: str,
        members: str,
        quiet: str = False,
        custom_message: Optional[str] = None
    ) -> DropboxResponse:
        """Allows an owner or editor (if the ACL update policy allows) of a shared

        API Endpoint: /2/sharing/add_folder_member
        Namespace: sharing
        Client type: user

        Args:
            shared_folder_id (str, required): Parameter for sharing_add_folder_member
            members (str, required): Parameter for sharing_add_folder_member
            quiet (str, optional): Parameter for sharing_add_folder_member
            custom_message (str, optional): Parameter for sharing_add_folder_member

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            folder to add another member. For the new member to get access to all
            the functionality for this folder, you will need to call
            :meth:`sharing_mount_folder` on their behalf.
            Route attributes:
            scope: sharing.write
            :param str shared_folder_id: The ID for the shared folder.
            :param List[:class:`dropbox.sharing.AddMember`] members: The intended
            list of members to add.  Added members will receive invites to join
            the shared folder.
            :param bool quiet: Whether added members should be notified via email
            and device notifications of their invite.
            :param Nullable[str] custom_message: Optional message to display to
            added members in their invitation.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.AddFolderMemberError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_add_folder_member(shared_folder_id, members, quiet=quiet, custom_message=custom_message))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_check_job_status(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Returns the status of an asynchronous job.

        API Endpoint: /2/sharing/check_job_status
        Namespace: sharing
        Client type: user

        Args:
            async_job_id (str, required): Parameter for sharing_check_job_status

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.sharing.JobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.PollError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_check_job_status(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_check_remove_member_job_status(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Returns the status of an asynchronous job for sharing a folder.

        API Endpoint: /2/sharing/check_remove_member_job_status
        Namespace: sharing
        Client type: user

        Args:
            async_job_id (str, required): Parameter for sharing_check_remove_member_job_status

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.sharing.RemoveMemberJobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.PollError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_check_remove_member_job_status(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_check_share_job_status(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Returns the status of an asynchronous job for sharing a folder.

        API Endpoint: /2/sharing/check_share_job_status
        Namespace: sharing
        Client type: user

        Args:
            async_job_id (str, required): Parameter for sharing_check_share_job_status

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.sharing.ShareFolderJobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.PollError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_check_share_job_status(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_create_shared_link(
        self,
        path: str,
        short_url: str = False,
        pending_upload: Optional[str] = None
    ) -> DropboxResponse:
        """Create a shared link. If a shared link already exists for the given

        API Endpoint: /2/sharing/create_shared_link
        Namespace: sharing
        Client type: user

        Args:
            path (str, required): Parameter for sharing_create_shared_link
            short_url (str, optional): Parameter for sharing_create_shared_link
            pending_upload (str, optional): Parameter for sharing_create_shared_link

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            path, that link is returned. Previously, it was technically possible to
            break a shared link by moving or renaming the corresponding file or
            folder. In the future, this will no longer be the case, so your app
            shouldn't rely on this behavior. Instead, if your app needs to revoke a
            shared link, use :meth:`sharing_revoke_shared_link`.
            Route attributes:
            scope: sharing.write
            :param str path: The path to share.
            :type short_url: bool
            :param Nullable[:class:`dropbox.sharing.PendingUploadMode`]
            pending_upload: If it's okay to share a path that does not yet
            exist, set this to either ``PendingUploadMode.file`` or
            ``PendingUploadMode.folder`` to indicate whether to assume it's a
            file or folder.
            :rtype: :class:`dropbox.sharing.PathLinkMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.CreateSharedLinkError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_create_shared_link(path, short_url=short_url, pending_upload=pending_upload))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_create_shared_link_with_settings(
        self,
        path: str,
        settings: Optional[str] = None,
        team_folder_id: Optional[str] = None,
        team_member_id: Optional[str] = None,
    ) -> DropboxResponse:
        """Create a shared link with custom settings. If no settings are given then

        API Endpoint: /2/sharing/create_shared_link_with_settings
        Namespace: sharing
        Client type: user

        Args:
            path (str, required): Parameter for sharing_create_shared_link_with_settings
            settings (str, optional): Parameter for sharing_create_shared_link_with_settings

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            the default visibility is ``RequestedVisibility.public`` (The resolved
            visibility, though, may depend on other aspects such as team and shared
            folder settings).
            Route attributes:
            scope: sharing.write
            :param str path: The path to be shared by the shared link.
            :param Nullable[:class:`dropbox.sharing.SharedLinkSettings`] settings:
            The requested settings for the newly created shared link.
            :rtype: :class:`dropbox.sharing.SharedLinkMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.CreateSharedLinkWithSettingsError`
        """

        try:
            client = await self._get_user_client(team_member_id)
            client_to_use = client
            if team_folder_id:
                client_to_use = client.with_path_root(
                    dropbox.common.PathRoot.namespace_id(team_folder_id)
                )
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client_to_use.sharing_create_shared_link_with_settings(path, settings=settings))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_get_file_metadata(
        self,
        file: str,
        actions: Optional[str] = None
    ) -> DropboxResponse:
        """Returns shared file metadata.

        API Endpoint: /2/sharing/get_file_metadata
        Namespace: sharing
        Client type: user

        Args:
            file (str, required): Parameter for sharing_get_file_metadata
            actions (str, optional): Parameter for sharing_get_file_metadata

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.read
            :param str file: The file to query.
            :param Nullable[List[:class:`dropbox.sharing.FileAction`]] actions: A
            list of `FileAction`s corresponding to `FilePermission`s that should
            appear in the  response's ``SharedFileMetadata.permissions`` field
            describing the actions the  authenticated user can perform on the
            file.
            :rtype: :class:`dropbox.sharing.SharedFileMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.GetFileMetadataError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_get_file_metadata(file, actions=actions))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_get_file_metadata_batch(
        self,
        files: str,
        actions: Optional[str] = None
    ) -> DropboxResponse:
        """Returns shared file metadata.

        API Endpoint: /2/sharing/get_file_metadata_batch
        Namespace: sharing
        Client type: user

        Args:
            files (str, required): Parameter for sharing_get_file_metadata_batch
            actions (str, optional): Parameter for sharing_get_file_metadata_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.read
            :param List[str] files: The files to query.
            :param Nullable[List[:class:`dropbox.sharing.FileAction`]] actions: A
            list of `FileAction`s corresponding to `FilePermission`s that should
            appear in the  response's ``SharedFileMetadata.permissions`` field
            describing the actions the  authenticated user can perform on the
            file.
            :rtype: List[:class:`dropbox.sharing.GetFileMetadataBatchResult`]
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.SharingUserError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_get_file_metadata_batch(files, actions=actions))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_get_folder_metadata(
        self,
        shared_folder_id: str,
        actions: Optional[str] = None,
        team_member_id: Optional[str] = None
    ) -> DropboxResponse:
        """Returns shared folder metadata by its folder ID.

        API Endpoint: /2/sharing/get_folder_metadata
        Namespace: sharing
        Client type: user

        Args:
            shared_folder_id (str, required): Parameter for sharing_get_folder_metadata
            actions (str, optional): Parameter for sharing_get_folder_metadata

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.read
            :param str shared_folder_id: The ID for the shared folder.
            :param Nullable[List[:class:`dropbox.sharing.FolderAction`]] actions: A
            list of `FolderAction`s corresponding to `FolderPermission`s that
            should appear in the  response's
            ``SharedFolderMetadata.permissions`` field describing the actions
            the  authenticated user can perform on the folder.
            :rtype: :class:`dropbox.sharing.SharedFolderMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.SharedFolderAccessError`
        """

        try:
            client = await self._get_user_client(team_member_id=team_member_id)
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_get_folder_metadata(shared_folder_id, actions=actions))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_get_shared_link_file(
        self,
        url: str,
        path: Optional[str] = None,
        link_password: Optional[str] = None
    ) -> DropboxResponse:
        """Download the shared link's file from a user's Dropbox.

        API Endpoint: /2/sharing/get_shared_link_file
        Namespace: sharing
        Client type: user

        Args:
            url (str, required): Parameter for sharing_get_shared_link_file
            path (str, optional): Parameter for sharing_get_shared_link_file
            link_password (str, optional): Parameter for sharing_get_shared_link_file

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.read
            :param str url: URL of the shared link.
            :param Nullable[str] path: If the shared link is to a folder, this
            parameter can be used to retrieve the metadata for a specific file
            or sub-folder in this folder. A relative path should be used.
            :param Nullable[str] link_password: If the shared link has a password,
            this parameter can be used.
            :rtype: (:class:`dropbox.sharing.SharedLinkMetadata`,
            :class:`requests.models.Response`)
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.GetSharedLinkFileError`
            If you do not consume the entire response body, then you must call close
            on the response object, otherwise you will max out your available
            connections. We recommend using the `contextlib.closing
            <https://docs.python.org/2/library/contextlib.html#contextlib.closing>`_
            context manager to ensure this.
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_get_shared_link_file(url, path=path, link_password=link_password))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_get_shared_link_file_to_file(
        self,
        download_path: str,
        url: str,
        path: Optional[str] = None,
        link_password: Optional[str] = None
    ) -> DropboxResponse:
        """Download the shared link's file from a user's Dropbox.

        API Endpoint: /2/sharing/get_shared_link_file_to_file
        Namespace: sharing
        Client type: user

        Args:
            download_path (str, required): Parameter for sharing_get_shared_link_file_to_file
            url (str, required): Parameter for sharing_get_shared_link_file_to_file
            path (str, optional): Parameter for sharing_get_shared_link_file_to_file
            link_password (str, optional): Parameter for sharing_get_shared_link_file_to_file

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.read
            :param str download_path: Path on local machine to save file.
            :param str url: URL of the shared link.
            :param Nullable[str] path: If the shared link is to a folder, this
            parameter can be used to retrieve the metadata for a specific file
            or sub-folder in this folder. A relative path should be used.
            :param Nullable[str] link_password: If the shared link has a password,
            this parameter can be used.
            :rtype: :class:`dropbox.sharing.SharedLinkMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.GetSharedLinkFileError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_get_shared_link_file_to_file(download_path, url, path=path, link_password=link_password))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_get_shared_link_metadata(
        self,
        url: str,
        path: Optional[str] = None,
        link_password: Optional[str] = None
    ) -> DropboxResponse:
        """Get the shared link's metadata.

        API Endpoint: /2/sharing/get_shared_link_metadata
        Namespace: sharing
        Client type: user

        Args:
            url (str, required): Parameter for sharing_get_shared_link_metadata
            path (str, optional): Parameter for sharing_get_shared_link_metadata
            link_password (str, optional): Parameter for sharing_get_shared_link_metadata

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.read
            :param str url: URL of the shared link.
            :param Nullable[str] path: If the shared link is to a folder, this
            parameter can be used to retrieve the metadata for a specific file
            or sub-folder in this folder. A relative path should be used.
            :param Nullable[str] link_password: If the shared link has a password,
            this parameter can be used.
            :rtype: :class:`dropbox.sharing.SharedLinkMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.SharedLinkError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_get_shared_link_metadata(url, path=path, link_password=link_password))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_get_shared_links(
        self,
        path: Optional[str] = None
    ) -> DropboxResponse:
        """Returns a list of :class:`dropbox.sharing.LinkMetadata` objects for this

        API Endpoint: /2/sharing/get_shared_links
        Namespace: sharing
        Client type: user

        Args:
            path (str, optional): Parameter for sharing_get_shared_links

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            user, including collection links. If no path is given, returns a list of
            all shared links for the current user, including collection links, up to
            a maximum of 1000 links. If a non-empty path is given, returns a list of
            all shared links that allow access to the given path.  Collection links
            are never returned in this case.
            Route attributes:
            scope: sharing.read
            :param Nullable[str] path: See :meth:`sharing_get_shared_links`
            description.
            :rtype: :class:`dropbox.sharing.GetSharedLinksResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.GetSharedLinksError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_get_shared_links(path=path))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_list_file_members(
        self,
        file: str,
        actions: Optional[str] = None,
        include_inherited: str = True,
        limit: str = 100,
        team_member_id: Optional[str] = None
    ) -> DropboxResponse:
        """Use to obtain the members who have been invited to a file, both

        API Endpoint: /2/sharing/list_file_members
        Namespace: sharing
        Client type: user

        Args:
            file (str, required): Parameter for sharing_list_file_members
            actions (str, optional): Parameter for sharing_list_file_members
            include_inherited (str, optional): Parameter for sharing_list_file_members
            limit (str, optional): Parameter for sharing_list_file_members

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            inherited and uninherited members.
            Route attributes:
            scope: sharing.read
            :param str file: The file for which you want to see members.
            :param Nullable[List[:class:`dropbox.sharing.MemberAction`]] actions:
            The actions for which to return permissions on a member.
            :param bool include_inherited: Whether to include members who only have
            access from a parent shared folder.
            :param int limit: Number of members to return max per query. Defaults to
            100 if no limit is specified.
            :rtype: :class:`dropbox.sharing.SharedFileMembers`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.ListFileMembersError`
        """
        client = await self._get_user_client(team_member_id=team_member_id)
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_list_file_members(file, actions=actions, include_inherited=include_inherited, limit=limit))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_list_file_members_batch(
        self,
        files: str,
        limit: str = 10
    ) -> DropboxResponse:
        """Get members of multiple files at once. The arguments to this route are

        API Endpoint: /2/sharing/list_file_members_batch
        Namespace: sharing
        Client type: user

        Args:
            files (str, required): Parameter for sharing_list_file_members_batch
            limit (str, optional): Parameter for sharing_list_file_members_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            more limited, and the limit on query result size per file is more
            strict. To customize the results more, use the individual file endpoint.
            Inherited users and groups are not included in the result, and
            permissions are not returned for this endpoint.
            Route attributes:
            scope: sharing.read
            :param List[str] files: Files for which to return members.
            :param int limit: Number of members to return max per query. Defaults to
            10 if no limit is specified.
            :rtype: List[:class:`dropbox.sharing.ListFileMembersBatchResult`]
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.SharingUserError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_list_file_members_batch(files, limit=limit))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_list_file_members_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from :meth:`sharing_list_file_members`

        API Endpoint: /2/sharing/list_file_members_continue
        Namespace: sharing
        Client type: user

        Args:
            cursor (str, required): Parameter for sharing_list_file_members_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            or :meth:`sharing_list_file_members_batch`, use this to paginate through
            all shared file members.
            Route attributes:
            scope: sharing.read
            :param str cursor: The cursor returned by your last call to
            :meth:`sharing_list_file_members`,
            :meth:`sharing_list_file_members_continue`, or
            :meth:`sharing_list_file_members_batch`.
            :rtype: :class:`dropbox.sharing.SharedFileMembers`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.ListFileMembersContinueError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_list_file_members_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_list_folder_members(
        self,
        shared_folder_id: str,
        actions: Optional[str] = None,
        limit: str = 1000,
        team_member_id: Optional[str] = None,
        as_admin: bool = False
    ) -> DropboxResponse:
        """Returns shared folder membership by its folder ID.

        API Endpoint: /2/sharing/list_folder_members
        Namespace: sharing
        Client type: user

        Args:
            shared_folder_id (str, required): Parameter for sharing_list_folder_members
            actions (str, optional): Parameter for sharing_list_folder_members
            limit (str, optional): Parameter for sharing_list_folder_members

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.read
            :param str shared_folder_id: The ID for the shared folder.
            :rtype: :class:`dropbox.sharing.SharedFolderMembers`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.SharedFolderAccessError`
        """

        try:
            client = await self._get_user_client(team_member_id=team_member_id, as_admin=as_admin)
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_list_folder_members(shared_folder_id, actions=actions, limit=limit))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_list_folder_members_continue(
        self,
        cursor: str,
        team_member_id: Optional[str] = None,
        as_admin: bool = False
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from

        API Endpoint: /2/sharing/list_folder_members_continue
        Namespace: sharing
        Client type: user

        Args:
            cursor (str, required): Parameter for sharing_list_folder_members_continue
            team_member_id (str, optional): Parameter for sharing_list_folder_members_continue
            as_admin (bool, optional): Parameter for sharing_list_folder_members_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`sharing_list_folder_members`, use this to paginate through all
            shared folder members.
            Route attributes:
            scope: sharing.read
            :param str cursor: The cursor returned by your last call to
            :meth:`sharing_list_folder_members` or
            :meth:`sharing_list_folder_members_continue`.
            :rtype: :class:`dropbox.sharing.SharedFolderMembers`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.ListFolderMembersContinueError`
        """
        try:
            client = await self._get_user_client(team_member_id=team_member_id, as_admin=as_admin)
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_list_folder_members_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_list_folders(
        self,
        limit: str = 1000,
        actions: Optional[str] = None,
        team_member_id: Optional[str] = None
    ) -> DropboxResponse:
        """Return the list of all shared folders the current user has access to.

        API Endpoint: /2/sharing/list_folders
        Namespace: sharing
        Client type: user

        Args:
            limit (str, optional): Parameter for sharing_list_folders
            actions (str, optional): Parameter for sharing_list_folders

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.read
            :param int limit: The maximum number of results to return per request.
            :param Nullable[List[:class:`dropbox.sharing.FolderAction`]] actions: A
            list of `FolderAction`s corresponding to `FolderPermission`s that
            should appear in the  response's
            ``SharedFolderMetadata.permissions`` field describing the actions
            the  authenticated user can perform on the folder.
            :rtype: :class:`dropbox.sharing.ListFoldersResult`
        """
        try:
            client = await self._get_user_client(team_member_id)
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_list_folders(limit=limit, actions=actions))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_list_folders_continue(
        self,
        cursor: str,
        team_member_id: Optional[str] = None
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from :meth:`sharing_list_folders`, use

        API Endpoint: /2/sharing/list_folders_continue
        Namespace: sharing
        Client type: user

        Args:
            cursor (str, required): Parameter for sharing_list_folders_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to paginate through all shared folders. The cursor must come from a
            previous call to :meth:`sharing_list_folders` or
            :meth:`sharing_list_folders_continue`.
            Route attributes:
            scope: sharing.read
            :param str cursor: The cursor returned by the previous API call
            specified in the endpoint description.
            :rtype: :class:`dropbox.sharing.ListFoldersResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.ListFoldersContinueError`
        """
        try:
            client = await self._get_user_client(team_member_id)
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_list_folders_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_list_mountable_folders(
        self,
        limit: str = 1000,
        actions: Optional[str] = None
    ) -> DropboxResponse:
        """Return the list of all shared folders the current user can mount or

        API Endpoint: /2/sharing/list_mountable_folders
        Namespace: sharing
        Client type: user

        Args:
            limit (str, optional): Parameter for sharing_list_mountable_folders
            actions (str, optional): Parameter for sharing_list_mountable_folders

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            unmount.
            Route attributes:
            scope: sharing.read
            :param int limit: The maximum number of results to return per request.
            :param Nullable[List[:class:`dropbox.sharing.FolderAction`]] actions: A
            list of `FolderAction`s corresponding to `FolderPermission`s that
            should appear in the  response's
            ``SharedFolderMetadata.permissions`` field describing the actions
            the  authenticated user can perform on the folder.
            :rtype: :class:`dropbox.sharing.ListFoldersResult`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_list_mountable_folders(limit=limit, actions=actions))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_list_mountable_folders_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from

        API Endpoint: /2/sharing/list_mountable_folders_continue
        Namespace: sharing
        Client type: user

        Args:
            cursor (str, required): Parameter for sharing_list_mountable_folders_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`sharing_list_mountable_folders`, use this to paginate through all
            mountable shared folders. The cursor must come from a previous call to
            :meth:`sharing_list_mountable_folders` or
            :meth:`sharing_list_mountable_folders_continue`.
            Route attributes:
            scope: sharing.read
            :param str cursor: The cursor returned by the previous API call
            specified in the endpoint description.
            :rtype: :class:`dropbox.sharing.ListFoldersResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.ListFoldersContinueError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_list_mountable_folders_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_list_received_files(
        self,
        limit: str = 100,
        actions: Optional[str] = None
    ) -> DropboxResponse:
        """Returns a list of all files shared with current user.  Does not include

        API Endpoint: /2/sharing/list_received_files
        Namespace: sharing
        Client type: user

        Args:
            limit (str, optional): Parameter for sharing_list_received_files
            actions (str, optional): Parameter for sharing_list_received_files

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            files the user has received via shared folders, and does  not include
            unclaimed invitations.
            Route attributes:
            scope: sharing.read
            :param int limit: Number of files to return max per query. Defaults to
            100 if no limit is specified.
            :param Nullable[List[:class:`dropbox.sharing.FileAction`]] actions: A
            list of `FileAction`s corresponding to `FilePermission`s that should
            appear in the  response's ``SharedFileMetadata.permissions`` field
            describing the actions the  authenticated user can perform on the
            file.
            :rtype: :class:`dropbox.sharing.ListFilesResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.SharingUserError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_list_received_files(limit=limit, actions=actions))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_list_received_files_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Get more results with a cursor from :meth:`sharing_list_received_files`.

        API Endpoint: /2/sharing/list_received_files_continue
        Namespace: sharing
        Client type: user

        Args:
            cursor (str, required): Parameter for sharing_list_received_files_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.read
            :param str cursor: Cursor in ``ListFilesResult.cursor``.
            :rtype: :class:`dropbox.sharing.ListFilesResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.ListFilesContinueError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_list_received_files_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_list_shared_links(
        self,
        path: Optional[str] = None,
        cursor: Optional[str] = None,
        direct_only: Optional[str] = None
    ) -> DropboxResponse:
        """List shared links of this user. If no path is given, returns a list of

        API Endpoint: /2/sharing/list_shared_links
        Namespace: sharing
        Client type: user

        Args:
            path (str, optional): Parameter for sharing_list_shared_links
            cursor (str, optional): Parameter for sharing_list_shared_links
            direct_only (str, optional): Parameter for sharing_list_shared_links

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            all shared links for the current user. For members of business teams
            using team space and member folders, returns all shared links in the
            team member's home folder unless the team space ID is specified in the
            request header. For more information, refer to the `Namespace Guide
            <https://www.dropbox.com/developers/reference/namespace-guide>`_. If a
            non-empty path is given, returns a list of all shared links that allow
            access to the given path - direct links to the given path and links to
            parent folders of the given path. Links to parent folders can be
            suppressed by setting direct_only to true.
            Route attributes:
            scope: sharing.read
            :param Nullable[str] path: See :meth:`sharing_list_shared_links`
            description.
            :param Nullable[str] cursor: The cursor returned by your last call to
            :meth:`sharing_list_shared_links`.
            :param Nullable[bool] direct_only: See :meth:`sharing_list_shared_links`
            description.
            :rtype: :class:`dropbox.sharing.ListSharedLinksResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.ListSharedLinksError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_list_shared_links(path=path, cursor=cursor, direct_only=direct_only))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_modify_shared_link_settings(
        self,
        url: str,
        settings: str,
        remove_expiration: str = False
    ) -> DropboxResponse:
        """Modify the shared link's settings. If the requested visibility conflict

        API Endpoint: /2/sharing/modify_shared_link_settings
        Namespace: sharing
        Client type: user

        Args:
            url (str, required): Parameter for sharing_modify_shared_link_settings
            settings (str, required): Parameter for sharing_modify_shared_link_settings
            remove_expiration (str, optional): Parameter for sharing_modify_shared_link_settings

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            with the shared links policy of the team or the shared folder (in case
            the linked file is part of a shared folder) then the
            ``LinkPermissions.resolved_visibility`` of the returned
            :class:`dropbox.sharing.SharedLinkMetadata` will reflect the actual
            visibility of the shared link and the
            ``LinkPermissions.requested_visibility`` will reflect the requested
            visibility.
            Route attributes:
            scope: sharing.write
            :param str url: URL of the shared link to change its settings.
            :param settings: Set of settings for the shared link.
            :type settings: :class:`dropbox.sharing.SharedLinkSettings`
            :param bool remove_expiration: If set to true, removes the expiration of
            the shared link.
            :rtype: :class:`dropbox.sharing.SharedLinkMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.ModifySharedLinkSettingsError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_modify_shared_link_settings(url, settings, remove_expiration=remove_expiration))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_mount_folder(
        self,
        shared_folder_id: str
    ) -> DropboxResponse:
        """The current user mounts the designated folder. Mount a shared folder for

        API Endpoint: /2/sharing/mount_folder
        Namespace: sharing
        Client type: user

        Args:
            shared_folder_id (str, required): Parameter for sharing_mount_folder

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            a user after they have been added as a member. Once mounted, the shared
            folder will appear in their Dropbox.
            Route attributes:
            scope: sharing.write
            :param str shared_folder_id: The ID of the shared folder to mount.
            :rtype: :class:`dropbox.sharing.SharedFolderMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.MountFolderError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_mount_folder(shared_folder_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_relinquish_file_membership(
        self,
        file: str
    ) -> DropboxResponse:
        """The current user relinquishes their membership in the designated file.

        API Endpoint: /2/sharing/relinquish_file_membership
        Namespace: sharing
        Client type: user

        Args:
            file (str, required): Parameter for sharing_relinquish_file_membership

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Note that the current user may still have inherited access to this file
            through the parent folder.
            Route attributes:
            scope: sharing.write
            :param str file: The path or id for the file.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.RelinquishFileMembershipError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_relinquish_file_membership(file))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_relinquish_folder_membership(
        self,
        shared_folder_id: str,
        leave_a_copy: str = False
    ) -> DropboxResponse:
        """The current user relinquishes their membership in the designated shared

        API Endpoint: /2/sharing/relinquish_folder_membership
        Namespace: sharing
        Client type: user

        Args:
            shared_folder_id (str, required): Parameter for sharing_relinquish_folder_membership
            leave_a_copy (str, optional): Parameter for sharing_relinquish_folder_membership

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            folder and will no longer have access to the folder.  A folder owner
            cannot relinquish membership in their own folder. This will run
            synchronously if leave_a_copy is false, and asynchronously if
            leave_a_copy is true.
            Route attributes:
            scope: sharing.write
            :param str shared_folder_id: The ID for the shared folder.
            :param bool leave_a_copy: Keep a copy of the folder's contents upon
            relinquishing membership. This must be set to false when the folder
            is within a team folder or another shared folder.
            :rtype: :class:`dropbox.sharing.LaunchEmptyResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.RelinquishFolderMembershipError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_relinquish_folder_membership(shared_folder_id, leave_a_copy=leave_a_copy))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_remove_file_member(
        self,
        file: str,
        member: str
    ) -> DropboxResponse:
        """Identical to remove_file_member_2 but with less information returned.

        API Endpoint: /2/sharing/remove_file_member
        Namespace: sharing
        Client type: user

        Args:
            file (str, required): Parameter for sharing_remove_file_member
            member (str, required): Parameter for sharing_remove_file_member

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.write
            :param str file: File from which to remove members.
            :param member: Member to remove from this file. Note that even if an
            email is specified, it may result in the removal of a user (not an
            invitee) if the user's main account corresponds to that email
            address.
            :type member: :class:`dropbox.sharing.MemberSelector`
            :rtype: :class:`dropbox.sharing.FileMemberActionIndividualResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.RemoveFileMemberError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_remove_file_member(file, member))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_remove_file_member_2(
        self,
        file: str,
        member: str
    ) -> DropboxResponse:
        """Removes a specified member from the file.

        API Endpoint: /2/sharing/remove_file_member_2
        Namespace: sharing
        Client type: user

        Args:
            file (str, required): Parameter for sharing_remove_file_member_2
            member (str, required): Parameter for sharing_remove_file_member_2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.write
            :param str file: File from which to remove members.
            :param member: Member to remove from this file. Note that even if an
            email is specified, it may result in the removal of a user (not an
            invitee) if the user's main account corresponds to that email
            address.
            :type member: :class:`dropbox.sharing.MemberSelector`
            :rtype: :class:`dropbox.sharing.FileMemberRemoveActionResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.RemoveFileMemberError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_remove_file_member_2(file, member))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_remove_folder_member(
        self,
        shared_folder_id: str,
        member: str,
        leave_a_copy: str
    ) -> DropboxResponse:
        """Allows an owner or editor (if the ACL update policy allows) of a shared

        API Endpoint: /2/sharing/remove_folder_member
        Namespace: sharing
        Client type: user

        Args:
            shared_folder_id (str, required): Parameter for sharing_remove_folder_member
            member (str, required): Parameter for sharing_remove_folder_member
            leave_a_copy (str, required): Parameter for sharing_remove_folder_member

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            folder to remove another member.
            Route attributes:
            scope: sharing.write
            :param str shared_folder_id: The ID for the shared folder.
            :param member: The member to remove from the folder.
            :type member: :class:`dropbox.sharing.MemberSelector`
            :param bool leave_a_copy: If true, the removed user will keep their copy
            of the folder after it's unshared, assuming it was mounted.
            Otherwise, it will be removed from their Dropbox. This must be set
            to false when removing a group, or when the folder is within a team
            folder or another shared folder.
            :rtype: :class:`dropbox.sharing.LaunchResultBase`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.RemoveFolderMemberError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_remove_folder_member(shared_folder_id, member, leave_a_copy))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_revoke_shared_link(
        self,
        url: str
    ) -> DropboxResponse:
        """Revoke a shared link. Note that even after revoking a shared link to a

        API Endpoint: /2/sharing/revoke_shared_link
        Namespace: sharing
        Client type: user

        Args:
            url (str, required): Parameter for sharing_revoke_shared_link

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            file, the file may be accessible if there are shared links leading to
            any of the file parent folders. To list all shared links that enable
            access to a specific file, you can use the
            :meth:`sharing_list_shared_links` with the file as the
            ``ListSharedLinksArg.path`` argument.
            Route attributes:
            scope: sharing.write
            :param str url: URL of the shared link.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.RevokeSharedLinkError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_revoke_shared_link(url))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_set_access_inheritance(
        self,
        shared_folder_id: str,
        access_inheritance: str = AccessInheritance('inherit', None)
    ) -> DropboxResponse:
        """Change the inheritance policy of an existing Shared Folder. Only

        API Endpoint: /2/sharing/set_access_inheritance
        Namespace: sharing
        Client type: user

        Args:
            shared_folder_id (str, required): Parameter for sharing_set_access_inheritance
            access_inheritance (str, optional): Parameter for sharing_set_access_inheritance

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            permitted for shared folders in a shared team root. If a
            ``ShareFolderLaunch.async_job_id`` is returned, you'll need to call
            :meth:`sharing_check_share_job_status` until the action completes to get
            the metadata for the folder.
            Route attributes:
            scope: sharing.write
            :param access_inheritance: The access inheritance settings for the
            folder.
            :type access_inheritance: :class:`dropbox.sharing.AccessInheritance`
            :param str shared_folder_id: The ID for the shared folder.
            :rtype: :class:`dropbox.sharing.ShareFolderLaunch`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.SetAccessInheritanceError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_set_access_inheritance(shared_folder_id, access_inheritance=access_inheritance))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_share_folder(
        self,
        path: str,
        acl_update_policy: Optional[str] = None,
        force_async: str = False,
        member_policy: Optional[str] = None,
        shared_link_policy: Optional[str] = None,
        viewer_info_policy: Optional[str] = None,
        access_inheritance: str = AccessInheritance('inherit', None),
        actions: Optional[str] = None,
        link_settings: Optional[str] = None
    ) -> DropboxResponse:
        """Share a folder with collaborators. Most sharing will be completed

        API Endpoint: /2/sharing/share_folder
        Namespace: sharing
        Client type: user

        Args:
            path (str, required): Parameter for sharing_share_folder
            acl_update_policy (str, optional): Parameter for sharing_share_folder
            force_async (str, optional): Parameter for sharing_share_folder
            member_policy (str, optional): Parameter for sharing_share_folder
            shared_link_policy (str, optional): Parameter for sharing_share_folder
            viewer_info_policy (str, optional): Parameter for sharing_share_folder
            access_inheritance (str, optional): Parameter for sharing_share_folder
            actions (str, optional): Parameter for sharing_share_folder
            link_settings (str, optional): Parameter for sharing_share_folder

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            synchronously. Large folders will be completed asynchronously. To make
            testing the async case repeatable, set `ShareFolderArg.force_async`. If
            a ``ShareFolderLaunch.async_job_id`` is returned, you'll need to call
            :meth:`sharing_check_share_job_status` until the action completes to get
            the metadata for the folder.
            Route attributes:
            scope: sharing.write
            :param Nullable[List[:class:`dropbox.sharing.FolderAction`]] actions: A
            list of `FolderAction`s corresponding to `FolderPermission`s that
            should appear in the  response's
            ``SharedFolderMetadata.permissions`` field describing the actions
            the  authenticated user can perform on the folder.
            :param Nullable[:class:`dropbox.sharing.LinkSettings`] link_settings:
            Settings on the link for this folder.
            :rtype: :class:`dropbox.sharing.ShareFolderLaunch`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.ShareFolderError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_share_folder(path, acl_update_policy=acl_update_policy, force_async=force_async, member_policy=member_policy, shared_link_policy=shared_link_policy, viewer_info_policy=viewer_info_policy, access_inheritance=access_inheritance, actions=actions, link_settings=link_settings))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_transfer_folder(
        self,
        shared_folder_id: str,
        to_dropbox_id: str
    ) -> DropboxResponse:
        """Transfer ownership of a shared folder to a member of the shared folder.

        API Endpoint: /2/sharing/transfer_folder
        Namespace: sharing
        Client type: user

        Args:
            shared_folder_id (str, required): Parameter for sharing_transfer_folder
            to_dropbox_id (str, required): Parameter for sharing_transfer_folder

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            User must have ``AccessLevel.owner`` access to the shared folder to
            perform a transfer.
            Route attributes:
            scope: sharing.write
            :param str shared_folder_id: The ID for the shared folder.
            :param str to_dropbox_id: A account or team member ID to transfer
            ownership to.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.TransferFolderError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_transfer_folder(shared_folder_id, to_dropbox_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_unmount_folder(
        self,
        shared_folder_id: str
    ) -> DropboxResponse:
        """The current user unmounts the designated folder. They can re-mount the

        API Endpoint: /2/sharing/unmount_folder
        Namespace: sharing
        Client type: user

        Args:
            shared_folder_id (str, required): Parameter for sharing_unmount_folder

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            folder at a later time using :meth:`sharing_mount_folder`.
            Route attributes:
            scope: sharing.write
            :param str shared_folder_id: The ID for the shared folder.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.UnmountFolderError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_unmount_folder(shared_folder_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_unshare_file(
        self,
        file: str,
        team_folder_id: Optional[str] = None,
        team_member_id: Optional[str] = None
    ) -> DropboxResponse:
        """Remove all members from this file. Does not remove inherited members.

        API Endpoint: /2/sharing/unshare_file
        Namespace: sharing
        Client type: user

        Args:
            file (str, required): Parameter for sharing_unshare_file

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.write
            :param str file: The file to unshare.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.UnshareFileError`
        """
        try:
            client = await self._get_user_client(team_member_id)
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_unshare_file(file))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_unshare_folder(
        self,
        shared_folder_id: str,
        leave_a_copy: str = False
    ) -> DropboxResponse:
        """Allows a shared folder owner to unshare the folder. You'll need to call

        API Endpoint: /2/sharing/unshare_folder
        Namespace: sharing
        Client type: user

        Args:
            shared_folder_id (str, required): Parameter for sharing_unshare_folder
            leave_a_copy (str, optional): Parameter for sharing_unshare_folder

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`sharing_check_job_status` to determine if the action has
            completed successfully.
            Route attributes:
            scope: sharing.write
            :param str shared_folder_id: The ID for the shared folder.
            :param bool leave_a_copy: If true, members of this shared folder will
            get a copy of this folder after it's unshared. Otherwise, it will be
            removed from their Dropbox. The current user, who is an owner, will
            always retain their copy.
            :rtype: :class:`dropbox.sharing.LaunchEmptyResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.UnshareFolderError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_unshare_folder(shared_folder_id, leave_a_copy=leave_a_copy))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_update_file_member(
        self,
        file: str,
        member: str,
        access_level: str
    ) -> DropboxResponse:
        """Changes a member's access on a shared file.

        API Endpoint: /2/sharing/update_file_member
        Namespace: sharing
        Client type: user

        Args:
            file (str, required): Parameter for sharing_update_file_member
            member (str, required): Parameter for sharing_update_file_member
            access_level (str, required): Parameter for sharing_update_file_member

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.write
            :param str file: File for which we are changing a member's access.
            :param member: The member whose access we are changing.
            :type member: :class:`dropbox.sharing.MemberSelector`
            :param access_level: The new access level for the member.
            :type access_level: :class:`dropbox.sharing.AccessLevel`
            :rtype: :class:`dropbox.sharing.MemberAccessLevelResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.FileMemberActionError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_update_file_member(file, member, access_level))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_update_folder_member(
        self,
        shared_folder_id: str,
        member: str,
        access_level: str
    ) -> DropboxResponse:
        """Allows an owner or editor of a shared folder to update another member's

        API Endpoint: /2/sharing/update_folder_member
        Namespace: sharing
        Client type: user

        Args:
            shared_folder_id (str, required): Parameter for sharing_update_folder_member
            member (str, required): Parameter for sharing_update_folder_member
            access_level (str, required): Parameter for sharing_update_folder_member

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            permissions.
            Route attributes:
            scope: sharing.write
            :param str shared_folder_id: The ID for the shared folder.
            :param member: The member of the shared folder to update.  Only the
            ``MemberSelector.dropbox_id`` may be set at this time.
            :type member: :class:`dropbox.sharing.MemberSelector`
            :param access_level: The new access level for ``member``.
            ``AccessLevel.owner`` is disallowed.
            :type access_level: :class:`dropbox.sharing.AccessLevel`
            :rtype: :class:`dropbox.sharing.MemberAccessLevelResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.UpdateFolderMemberError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_update_folder_member(shared_folder_id, member, access_level))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def sharing_update_folder_policy(
        self,
        shared_folder_id: str,
        member_policy: Optional[str] = None,
        acl_update_policy: Optional[str] = None,
        viewer_info_policy: Optional[str] = None,
        shared_link_policy: Optional[str] = None,
        link_settings: Optional[str] = None,
        actions: Optional[str] = None
    ) -> DropboxResponse:
        """Update the sharing policies for a shared folder. User must have

        API Endpoint: /2/sharing/update_folder_policy
        Namespace: sharing
        Client type: user

        Args:
            shared_folder_id (str, required): Parameter for sharing_update_folder_policy
            member_policy (str, optional): Parameter for sharing_update_folder_policy
            acl_update_policy (str, optional): Parameter for sharing_update_folder_policy
            viewer_info_policy (str, optional): Parameter for sharing_update_folder_policy
            shared_link_policy (str, optional): Parameter for sharing_update_folder_policy
            link_settings (str, optional): Parameter for sharing_update_folder_policy
            actions (str, optional): Parameter for sharing_update_folder_policy

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            ``AccessLevel.owner`` access to the shared folder to update its
            policies.
            Route attributes:
            scope: sharing.write
            :param str shared_folder_id: The ID for the shared folder.
            :param Nullable[:class:`dropbox.sharing.MemberPolicy`] member_policy:
            Who can be a member of this shared folder. Only applicable if the
            current user is on a team.
            :param Nullable[:class:`dropbox.sharing.AclUpdatePolicy`]
            acl_update_policy: Who can add and remove members of this shared
            folder.
            :param Nullable[:class:`dropbox.sharing.ViewerInfoPolicy`]
            viewer_info_policy: Who can enable/disable viewer info for this
            shared folder.
            :param Nullable[:class:`dropbox.sharing.SharedLinkPolicy`]
            shared_link_policy: The policy to apply to shared links created for
            content inside this shared folder. The current user must be on a
            team to set this policy to ``SharedLinkPolicy.members``.
            :param Nullable[:class:`dropbox.sharing.LinkSettings`] link_settings:
            Settings on the link for this folder.
            :param Nullable[List[:class:`dropbox.sharing.FolderAction`]] actions: A
            list of `FolderAction`s corresponding to `FolderPermission`s that
            should appear in the  response's
            ``SharedFolderMetadata.permissions`` field describing the actions
            the  authenticated user can perform on the folder.
            :rtype: :class:`dropbox.sharing.SharedFolderMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.sharing.UpdateFolderPolicyError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.sharing_update_folder_policy(shared_folder_id, member_policy=member_policy, acl_update_policy=acl_update_policy, viewer_info_policy=viewer_info_policy, shared_link_policy=shared_link_policy, link_settings=link_settings, actions=actions))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def users_features_get_values(
        self,
        features: str
    ) -> DropboxResponse:
        """Get a list of feature values that may be configured for the current

        API Endpoint: /2/users/features_get_values
        Namespace: users
        Client type: user

        Args:
            features (str, required): Parameter for users_features_get_values

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            account.
            Route attributes:
            scope: account_info.read
            :param List[:class:`dropbox.users.UserFeature`] features: A list of
            features in :class:`dropbox.users.UserFeature`. If the list is
            empty, this route will return
            :class:`dropbox.users.UserFeaturesGetValuesBatchError`.
            :rtype: :class:`dropbox.users.UserFeaturesGetValuesBatchResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.users.UserFeaturesGetValuesBatchError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.users_features_get_values(features))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def users_get_account(
        self,
        account_id: str
    ) -> DropboxResponse:
        """Get information about a user's account.

        API Endpoint: /2/users/get_account
        Namespace: users
        Client type: user

        Args:
            account_id (str, required): Parameter for users_get_account

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sharing.read
            :param str account_id: A user's account identifier.
            :rtype: :class:`dropbox.users.BasicAccount`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.users.GetAccountError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.users_get_account(account_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def users_get_account_batch(
        self,
        account_ids: str
    ) -> DropboxResponse:
        """Get information about multiple user accounts.  At most 300 accounts may

        API Endpoint: /2/users/get_account_batch
        Namespace: users
        Client type: user

        Args:
            account_ids (str, required): Parameter for users_get_account_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            be queried per request.
            Route attributes:
            scope: sharing.read
            :param List[str] account_ids: List of user account identifiers.  Should
            not contain any duplicate account IDs.
            :rtype: List[:class:`dropbox.users.BasicAccount`]
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.users.GetAccountBatchError`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.users_get_account_batch(account_ids))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def users_get_current_account(self) -> DropboxResponse:
        """Get information about the current user's account.

        API Endpoint: /2/users/get_current_account
        Namespace: users
        Client type: user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: account_info.read
            :rtype: :class:`dropbox.users.FullAccount`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.users_get_current_account())
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def users_get_space_usage(self) -> DropboxResponse:
        """Get the space usage information for the current user's account.

        API Endpoint: /2/users/get_space_usage
        Namespace: users
        Client type: user

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: account_info.read
            :rtype: :class:`dropbox.users.SpaceUsage`
        """
        client = await self._get_user_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.users_get_space_usage())
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_file_properties_templates_add_for_team(
        self,
        name: str,
        description: str,
        fields: str
    ) -> DropboxResponse:
        """Add a template associated with a team. See

        API Endpoint: /2/team/file/properties_templates_add_for_team
        Namespace: file
        Client type: team

        Args:
            name (str, required): Parameter for file_properties_templates_add_for_team
            description (str, required): Parameter for file_properties_templates_add_for_team
            fields (str, required): Parameter for file_properties_templates_add_for_team

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`file_properties_properties_add` to add properties to a file or
            folder. Note: this endpoint will create team-owned templates.
            Route attributes:
            scope: files.team_metadata.write
            :rtype: :class:`dropbox.file_properties.AddTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.ModifyTemplateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.file_properties_templates_add_for_team(name, description, fields))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_file_properties_templates_get_for_team(
        self,
        template_id: str
    ) -> DropboxResponse:
        """Get the schema for a specified template.

        API Endpoint: /2/team/file/properties_templates_get_for_team
        Namespace: file
        Client type: team

        Args:
            template_id (str, required): Parameter for file_properties_templates_get_for_team

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.team_metadata.write
            :param str template_id: An identifier for template added by route  See
            :meth:`file_properties_templates_add_for_user` or
            :meth:`file_properties_templates_add_for_team`.
            :rtype: :class:`dropbox.file_properties.GetTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.TemplateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.file_properties_templates_get_for_team(template_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_file_properties_templates_list_for_team(self) -> DropboxResponse:
        """Get the template identifiers for a team. To get the schema of each

        API Endpoint: /2/team/file/properties_templates_list_for_team
        Namespace: file
        Client type: team

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            template use :meth:`file_properties_templates_get_for_team`.
            Route attributes:
            scope: files.team_metadata.write
            :rtype: :class:`dropbox.file_properties.ListTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.TemplateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.file_properties_templates_list_for_team())
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_file_properties_templates_remove_for_team(
        self,
        template_id: str
    ) -> DropboxResponse:
        """Permanently removes the specified template created from

        API Endpoint: /2/team/file/properties_templates_remove_for_team
        Namespace: file
        Client type: team

        Args:
            template_id (str, required): Parameter for file_properties_templates_remove_for_team

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`file_properties_templates_add_for_user`. All properties
            associated with the template will also be removed. This action cannot be
            undone.
            Route attributes:
            scope: files.team_metadata.write
            :param str template_id: An identifier for a template created by
            :meth:`file_properties_templates_add_for_user` or
            :meth:`file_properties_templates_add_for_team`.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.TemplateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.file_properties_templates_remove_for_team(template_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_file_properties_templates_update_for_team(
        self,
        template_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        add_fields: Optional[str] = None
    ) -> DropboxResponse:
        """Update a template associated with a team. This route can update the

        API Endpoint: /2/team/file/properties_templates_update_for_team
        Namespace: file
        Client type: team

        Args:
            template_id (str, required): Parameter for file_properties_templates_update_for_team
            name (str, optional): Parameter for file_properties_templates_update_for_team
            description (str, optional): Parameter for file_properties_templates_update_for_team
            add_fields (str, optional): Parameter for file_properties_templates_update_for_team

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            template name, the template description and add optional properties to
            templates.
            Route attributes:
            scope: files.team_metadata.write
            :param str template_id: An identifier for template added by  See
            :meth:`file_properties_templates_add_for_user` or
            :meth:`file_properties_templates_add_for_team`.
            :param Nullable[str] name: A display name for the template. template
            names can be up to 256 bytes.
            :param Nullable[str] description: Description for the new template.
            Template descriptions can be up to 1024 bytes.
            :param
            Nullable[List[:class:`dropbox.file_properties.PropertyFieldTemplate`]]
            add_fields: Property field templates to be added to the group
            template. There can be up to 32 properties in a single template.
            :rtype: :class:`dropbox.file_properties.UpdateTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.file_properties.ModifyTemplateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.file_properties_templates_update_for_team(template_id, name=name, description=description, add_fields=add_fields))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_devices_list_member_devices(
        self,
        team_member_id: str,
        include_web_sessions: str = True,
        include_desktop_clients: str = True,
        include_mobile_clients: str = True
    ) -> DropboxResponse:
        """List all device sessions of a team's member.

        API Endpoint: /2/team/devices/list_member_devices
        Namespace: team
        Client type: team

        Args:
            team_member_id (str, required): Parameter for team_devices_list_member_devices
            include_web_sessions (str, optional): Parameter for team_devices_list_member_devices
            include_desktop_clients (str, optional): Parameter for team_devices_list_member_devices
            include_mobile_clients (str, optional): Parameter for team_devices_list_member_devices

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sessions.list
            :param str team_member_id: The team's member id.
            :param bool include_web_sessions: Whether to list web sessions of the
            team's member.
            :param bool include_desktop_clients: Whether to list linked desktop
            devices of the team's member.
            :param bool include_mobile_clients: Whether to list linked mobile
            devices of the team's member.
            :rtype: :class:`dropbox.team.ListMemberDevicesResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.ListMemberDevicesError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_devices_list_member_devices(team_member_id, include_web_sessions=include_web_sessions, include_desktop_clients=include_desktop_clients, include_mobile_clients=include_mobile_clients))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_devices_list_members_devices(
        self,
        cursor: Optional[str] = None,
        include_web_sessions: str = True,
        include_desktop_clients: str = True,
        include_mobile_clients: str = True
    ) -> DropboxResponse:
        """List all device sessions of a team. Permission : Team member file

        API Endpoint: /2/team/devices/list_members_devices
        Namespace: team
        Client type: team

        Args:
            cursor (str, optional): Parameter for team_devices_list_members_devices
            include_web_sessions (str, optional): Parameter for team_devices_list_members_devices
            include_desktop_clients (str, optional): Parameter for team_devices_list_members_devices
            include_mobile_clients (str, optional): Parameter for team_devices_list_members_devices

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            access.
            Route attributes:
            scope: sessions.list
            :param Nullable[str] cursor: At the first call to the
            :meth:`team_devices_list_members_devices` the cursor shouldn't be
            passed. Then, if the result of the call includes a cursor, the
            following requests should include the received cursors in order to
            receive the next sub list of team devices.
            :param bool include_web_sessions: Whether to list web sessions of the
            team members.
            :param bool include_desktop_clients: Whether to list desktop clients of
            the team members.
            :param bool include_mobile_clients: Whether to list mobile clients of
            the team members.
            :rtype: :class:`dropbox.team.ListMembersDevicesResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.ListMembersDevicesError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_devices_list_members_devices(cursor=cursor, include_web_sessions=include_web_sessions, include_desktop_clients=include_desktop_clients, include_mobile_clients=include_mobile_clients))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_devices_list_team_devices(
        self,
        cursor: Optional[str] = None,
        include_web_sessions: str = True,
        include_desktop_clients: str = True,
        include_mobile_clients: str = True
    ) -> DropboxResponse:
        """List all device sessions of a team. Permission : Team member file

        API Endpoint: /2/team/devices/list_team_devices
        Namespace: team
        Client type: team

        Args:
            cursor (str, optional): Parameter for team_devices_list_team_devices
            include_web_sessions (str, optional): Parameter for team_devices_list_team_devices
            include_desktop_clients (str, optional): Parameter for team_devices_list_team_devices
            include_mobile_clients (str, optional): Parameter for team_devices_list_team_devices

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            access.
            Route attributes:
            scope: sessions.list
            :param Nullable[str] cursor: At the first call to the
            :meth:`team_devices_list_team_devices` the cursor shouldn't be
            passed. Then, if the result of the call includes a cursor, the
            following requests should include the received cursors in order to
            receive the next sub list of team devices.
            :param bool include_web_sessions: Whether to list web sessions of the
            team members.
            :param bool include_desktop_clients: Whether to list desktop clients of
            the team members.
            :param bool include_mobile_clients: Whether to list mobile clients of
            the team members.
            :rtype: :class:`dropbox.team.ListTeamDevicesResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.ListTeamDevicesError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_devices_list_team_devices(cursor=cursor, include_web_sessions=include_web_sessions, include_desktop_clients=include_desktop_clients, include_mobile_clients=include_mobile_clients))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_devices_revoke_device_session(
        self,
        arg: str
    ) -> DropboxResponse:
        """Revoke a device session of a team's member.

        API Endpoint: /2/team/devices/revoke_device_session
        Namespace: team
        Client type: team

        Args:
            arg (str, required): Parameter for team_devices_revoke_device_session

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sessions.modify
            :type arg: :class:`dropbox.team.RevokeDeviceSessionArg`
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.RevokeDeviceSessionError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_devices_revoke_device_session(arg))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_devices_revoke_device_session_batch(
        self,
        revoke_devices: str
    ) -> DropboxResponse:
        """Revoke a list of device sessions of team members.

        API Endpoint: /2/team/devices/revoke_device_session_batch
        Namespace: team
        Client type: team

        Args:
            revoke_devices (str, required): Parameter for team_devices_revoke_device_session_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sessions.modify
            :type revoke_devices: List[:class:`dropbox.team.RevokeDeviceSessionArg`]
            :rtype: :class:`dropbox.team.RevokeDeviceSessionBatchResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.RevokeDeviceSessionBatchError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_devices_revoke_device_session_batch(revoke_devices))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_features_get_values(
        self,
        features: str
    ) -> DropboxResponse:
        """Get the values for one or more featues. This route allows you to check

        API Endpoint: /2/team/features/get_values
        Namespace: team
        Client type: team

        Args:
            features (str, required): Parameter for team_features_get_values

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            your account's capability for what feature you can access or what value
            you have for certain features. Permission : Team information.
            Route attributes:
            scope: team_info.read
            :param List[:class:`dropbox.team.Feature`] features: A list of features
            in :class:`dropbox.team.Feature`. If the list is empty, this route
            will return :class:`dropbox.team.FeaturesGetValuesBatchError`.
            :rtype: :class:`dropbox.team.FeaturesGetValuesBatchResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.FeaturesGetValuesBatchError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_features_get_values(features))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_get_info(self) -> DropboxResponse:
        """Retrieves information about a team.

        API Endpoint: /2/team/get/info
        Namespace: team
        Client type: team

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: team_info.read
            :rtype: :class:`dropbox.team.TeamGetInfoResult`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_get_info())
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_groups_create(
        self,
        group_name: str,
        add_creator_as_owner: str = False,
        group_external_id: Optional[str] = None,
        group_management_type: Optional[str] = None
    ) -> DropboxResponse:
        """Creates a new, empty group, with a requested name. Permission : Team

        API Endpoint: /2/team/groups/create
        Namespace: team
        Client type: team

        Args:
            group_name (str, required): Parameter for team_groups_create
            add_creator_as_owner (str, optional): Parameter for team_groups_create
            group_external_id (str, optional): Parameter for team_groups_create
            group_management_type (str, optional): Parameter for team_groups_create

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            member management.
            Route attributes:
            scope: groups.write
            :param str group_name: Group name.
            :param bool add_creator_as_owner: Automatically add the creator of the
            group.
            :param Nullable[str] group_external_id: The creator of a team can
            associate an arbitrary external ID to the group.
            :param Nullable[:class:`dropbox.team.GroupManagementType`]
            group_management_type: Whether the team can be managed by selected
            users, or only by team admins.
            :rtype: :class:`dropbox.team.GroupFullInfo`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.GroupCreateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_groups_create(group_name, add_creator_as_owner=add_creator_as_owner, group_external_id=group_external_id, group_management_type=group_management_type))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_groups_delete(
        self,
        arg: str
    ) -> DropboxResponse:
        """Deletes a group. The group is deleted immediately. However the revoking

        API Endpoint: /2/team/groups/delete
        Namespace: team
        Client type: team

        Args:
            arg (str, required): Parameter for team_groups_delete

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            of group-owned resources may take additional time. Use the
            :meth:`team_groups_job_status_get` to determine whether this process has
            completed. Permission : Team member management.
            Route attributes:
            scope: groups.write
            :param arg: Argument for selecting a single group, either by group_id or
            by external group ID.
            :type arg: :class:`dropbox.team.GroupSelector`
            :rtype: :class:`dropbox.team.LaunchEmptyResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.GroupDeleteError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_groups_delete(arg))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_groups_get_info(
        self,
        arg: str
    ) -> DropboxResponse:
        """Retrieves information about one or more groups. Note that the optional

        API Endpoint: /2/team/groups/get_info
        Namespace: team
        Client type: team

        Args:
            arg (str, required): Parameter for team_groups_get_info

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            field  ``GroupFullInfo.members`` is not returned for system-managed
            groups. Permission : Team Information.
            Route attributes:
            scope: groups.read
            :param arg: Argument for selecting a list of groups, either by
            group_ids, or external group IDs.
            :type arg: :class:`dropbox.team.GroupsSelector`
            :rtype: List[:class:`dropbox.team.GroupsGetInfoItem`]
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.GroupsGetInfoError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_groups_get_info(arg))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_groups_job_status_get(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Once an async_job_id is returned from :meth:`team_groups_delete`,

        API Endpoint: /2/team/groups/job_status_get
        Namespace: team
        Client type: team

        Args:
            async_job_id (str, required): Parameter for team_groups_job_status_get

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`team_groups_members_add` , or :meth:`team_groups_members_remove`
            use this method to poll the status of granting/revoking group members'
            access to group-owned resources. Permission : Team member management.
            Route attributes:
            scope: groups.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.team.PollEmptyResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.GroupsPollError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_groups_job_status_get(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_groups_list(
        self,
        limit: str = 1000
    ) -> DropboxResponse:
        """Lists groups on a team. Permission : Team Information.

        API Endpoint: /2/team/groups/list
        Namespace: team
        Client type: team

        Args:
            limit (str, optional): Parameter for team_groups_list

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: groups.read
            :param int limit: Number of results to return per call.
            :rtype: :class:`dropbox.team.GroupsListResult`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_groups_list(limit=limit))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_groups_list_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from :meth:`team_groups_list`, use this

        API Endpoint: /2/team/groups/list_continue
        Namespace: team
        Client type: team

        Args:
            cursor (str, required): Parameter for team_groups_list_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            to paginate through all groups. Permission : Team Information.
            Route attributes:
            scope: groups.read
            :param str cursor: Indicates from what point to get the next set of
            groups.
            :rtype: :class:`dropbox.team.GroupsListResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.GroupsListContinueError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_groups_list_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_groups_members_add(
        self,
        group: str,
        members: str,
        return_members: str = True
    ) -> DropboxResponse:
        """Adds members to a group. The members are added immediately. However the

        API Endpoint: /2/team/groups/members_add
        Namespace: team
        Client type: team

        Args:
            group (str, required): Parameter for team_groups_members_add
            members (str, required): Parameter for team_groups_members_add
            return_members (str, optional): Parameter for team_groups_members_add

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            granting of group-owned resources may take additional time. Use the
            :meth:`team_groups_job_status_get` to determine whether this process has
            completed. Permission : Team member management.
            Route attributes:
            scope: groups.write
            :param group: Group to which users will be added.
            :type group: :class:`dropbox.team.GroupSelector`
            :param List[:class:`dropbox.team.MemberAccess`] members: List of users
            to be added to the group.
            :rtype: :class:`dropbox.team.GroupMembersChangeResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.GroupMembersAddError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_groups_members_add(group, members, return_members=return_members))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_groups_members_list(
            self,
            group: str,  # The group_id (string) is the correct input
            limit: int = 1000  # This must be an integer
        ) -> DropboxResponse:
        """Lists members of a group. Permission : Team Information.

        API Endpoint: /2/team/groups/members/list
        """
        client = await self._get_team_client()
        try:
            # 1. Create the GroupSelector object from the ID string
            group_selector = GroupSelector.group_id(group)

            loop = asyncio.get_running_loop()

            # 2. Pass the selector object and integer limit
            response = await loop.run_in_executor(
                None,
                lambda: client.team_groups_members_list(
                    group=group_selector,
                    limit=limit
                )
            )
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_groups_members_list_continue(
            self,
            cursor: str
        ) -> DropboxResponse:
        """Paginates through members of a group.

        API Endpoint: /2/team/groups/members/list/continue
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.team_groups_members_list_continue(cursor=cursor)
            )
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_groups_members_remove(
        self,
        group: str,
        users: str,
        return_members: str = True
    ) -> DropboxResponse:
        """Removes members from a group. The members are removed immediately.

        API Endpoint: /2/team/groups/members_remove
        Namespace: team
        Client type: team

        Args:
            group (str, required): Parameter for team_groups_members_remove
            users (str, required): Parameter for team_groups_members_remove
            return_members (str, optional): Parameter for team_groups_members_remove

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            However the revoking of group-owned resources may take additional time.
            Use the :meth:`team_groups_job_status_get` to determine whether this
            process has completed. This method permits removing the only owner of a
            group, even in cases where this is not possible via the web client.
            Permission : Team member management.
            Route attributes:
            scope: groups.write
            :param group: Group from which users will be removed.
            :type group: :class:`dropbox.team.GroupSelector`
            :param List[:class:`dropbox.team.UserSelectorArg`] users: List of users
            to be removed from the group.
            :rtype: :class:`dropbox.team.GroupMembersChangeResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.GroupMembersRemoveError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_groups_members_remove(group, users, return_members=return_members))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_groups_members_set_access_type(
        self,
        group: str,
        user: str,
        access_type: str,
        return_members: str = True
    ) -> DropboxResponse:
        """Sets a member's access type in a group. Permission : Team member

        API Endpoint: /2/team/groups/members_set_access_type
        Namespace: team
        Client type: team

        Args:
            group (str, required): Parameter for team_groups_members_set_access_type
            user (str, required): Parameter for team_groups_members_set_access_type
            access_type (str, required): Parameter for team_groups_members_set_access_type
            return_members (str, optional): Parameter for team_groups_members_set_access_type

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            management.
            Route attributes:
            scope: groups.write
            :param access_type: New group access type the user will have.
            :type access_type: :class:`dropbox.team.GroupAccessType`
            :param bool return_members: Whether to return the list of members in the
            group.  Note that the default value will cause all the group members
            to be returned in the response. This may take a long time for large
            groups.
            :rtype: List[:class:`dropbox.team.GroupsGetInfoItem`]
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.GroupMemberSetAccessTypeError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_groups_members_set_access_type(group, user, access_type, return_members=return_members))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_groups_update(
        self,
        group: str,
        return_members: str = True,
        new_group_name: Optional[str] = None,
        new_group_external_id: Optional[str] = None,
        new_group_management_type: Optional[str] = None
    ) -> DropboxResponse:
        """Updates a group's name and/or external ID. Permission : Team member

        API Endpoint: /2/team/groups/update
        Namespace: team
        Client type: team

        Args:
            group (str, required): Parameter for team_groups_update
            return_members (str, optional): Parameter for team_groups_update
            new_group_name (str, optional): Parameter for team_groups_update
            new_group_external_id (str, optional): Parameter for team_groups_update
            new_group_management_type (str, optional): Parameter for team_groups_update

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            management.
            Route attributes:
            scope: groups.write
            :param group: Specify a group.
            :type group: :class:`dropbox.team.GroupSelector`
            :param Nullable[str] new_group_name: Optional argument. Set group name
            to this if provided.
            :param Nullable[str] new_group_external_id: Optional argument. New group
            external ID. If the argument is None, the group's external_id won't
            be updated. If the argument is empty string, the group's external id
            will be cleared.
            :param Nullable[:class:`dropbox.team.GroupManagementType`]
            new_group_management_type: Set new group management type, if
            provided.
            :rtype: :class:`dropbox.team.GroupFullInfo`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.GroupUpdateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_groups_update(group, return_members=return_members, new_group_name=new_group_name, new_group_external_id=new_group_external_id, new_group_management_type=new_group_management_type))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_legal_holds_create_policy(
        self,
        name: str,
        members: str,
        description: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> DropboxResponse:
        """Creates new legal hold policy. Note: Legal Holds is a paid add-on. Not

        API Endpoint: /2/team/legal/holds_create_policy
        Namespace: team
        Client type: team

        Args:
            name (str, required): Parameter for team_legal_holds_create_policy
            members (str, required): Parameter for team_legal_holds_create_policy
            description (str, optional): Parameter for team_legal_holds_create_policy
            start_date (str, optional): Parameter for team_legal_holds_create_policy
            end_date (str, optional): Parameter for team_legal_holds_create_policy

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            all teams have the feature. Permission : Team member file access.
            Route attributes:
            scope: team_data.governance.write
            :param str name: Policy name.
            :param Nullable[str] description: A description of the legal hold
            policy.
            :param List[str] members: List of team member IDs added to the hold.
            :param Nullable[datetime] start_date: start date of the legal hold
            policy.
            :param Nullable[datetime] end_date: end date of the legal hold policy.
            :rtype: :class:`dropbox.team.LegalHoldPolicy`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.LegalHoldsPolicyCreateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_legal_holds_create_policy(name, members, description=description, start_date=start_date, end_date=end_date))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_legal_holds_get_policy(
        self,
        id: str
    ) -> DropboxResponse:
        """Gets a legal hold by Id. Note: Legal Holds is a paid add-on. Not all

        API Endpoint: /2/team/legal/holds_get_policy
        Namespace: team
        Client type: team

        Args:
            id (str, required): Parameter for team_legal_holds_get_policy

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            teams have the feature. Permission : Team member file access.
            Route attributes:
            scope: team_data.governance.write
            :param str id: The legal hold Id.
            :rtype: :class:`dropbox.team.LegalHoldPolicy`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.LegalHoldsGetPolicyError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_legal_holds_get_policy(id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_legal_holds_list_held_revisions(
        self,
        id: str
    ) -> DropboxResponse:
        """List the file metadata that's under the hold. Note: Legal Holds is a

        API Endpoint: /2/team/legal/holds_list_held_revisions
        Namespace: team
        Client type: team

        Args:
            id (str, required): Parameter for team_legal_holds_list_held_revisions

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            paid add-on. Not all teams have the feature. Permission : Team member
            file access.
            Route attributes:
            scope: team_data.governance.write
            :param str id: The legal hold Id.
            :rtype: :class:`dropbox.team.LegalHoldsListHeldRevisionResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.LegalHoldsListHeldRevisionsError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_legal_holds_list_held_revisions(id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_legal_holds_list_held_revisions_continue(
        self,
        id: str,
        cursor: Optional[str] = None
    ) -> DropboxResponse:
        """Continue listing the file metadata that's under the hold. Note: Legal

        API Endpoint: /2/team/legal/holds_list_held_revisions_continue
        Namespace: team
        Client type: team

        Args:
            id (str, required): Parameter for team_legal_holds_list_held_revisions_continue
            cursor (str, optional): Parameter for team_legal_holds_list_held_revisions_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Holds is a paid add-on. Not all teams have the feature. Permission :
            Team member file access.
            Route attributes:
            scope: team_data.governance.write
            :param str id: The legal hold Id.
            :param Nullable[str] cursor: The cursor idicates where to continue
            reading file metadata entries for the next API call. When there are
            no more entries, the cursor will return none.
            :rtype: :class:`dropbox.team.LegalHoldsListHeldRevisionResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.LegalHoldsListHeldRevisionsError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_legal_holds_list_held_revisions_continue(id, cursor=cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_legal_holds_list_policies(
        self,
        include_released: str = False
    ) -> DropboxResponse:
        """Lists legal holds on a team. Note: Legal Holds is a paid add-on. Not all

        API Endpoint: /2/team/legal/holds_list_policies
        Namespace: team
        Client type: team

        Args:
            include_released (str, optional): Parameter for team_legal_holds_list_policies

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            teams have the feature. Permission : Team member file access.
            Route attributes:
            scope: team_data.governance.write
            :param bool include_released: Whether to return holds that were
            released.
            :rtype: :class:`dropbox.team.LegalHoldsListPoliciesResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.LegalHoldsListPoliciesError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_legal_holds_list_policies(include_released=include_released))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_legal_holds_release_policy(
        self,
        id: str
    ) -> DropboxResponse:
        """Releases a legal hold by Id. Note: Legal Holds is a paid add-on. Not all

        API Endpoint: /2/team/legal/holds_release_policy
        Namespace: team
        Client type: team

        Args:
            id (str, required): Parameter for team_legal_holds_release_policy

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            teams have the feature. Permission : Team member file access.
            Route attributes:
            scope: team_data.governance.write
            :param str id: The legal hold Id.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.LegalHoldsPolicyReleaseError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_legal_holds_release_policy(id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_legal_holds_update_policy(
        self,
        id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        members: Optional[str] = None
    ) -> DropboxResponse:
        """Updates a legal hold. Note: Legal Holds is a paid add-on. Not all teams

        API Endpoint: /2/team/legal/holds_update_policy
        Namespace: team
        Client type: team

        Args:
            id (str, required): Parameter for team_legal_holds_update_policy
            name (str, optional): Parameter for team_legal_holds_update_policy
            description (str, optional): Parameter for team_legal_holds_update_policy
            members (str, optional): Parameter for team_legal_holds_update_policy

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            have the feature. Permission : Team member file access.
            Route attributes:
            scope: team_data.governance.write
            :param str id: The legal hold Id.
            :param Nullable[str] name: Policy new name.
            :param Nullable[str] description: Policy new description.
            :param Nullable[List[str]] members: List of team member IDs to apply the
            policy on.
            :rtype: :class:`dropbox.team.LegalHoldPolicy`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.LegalHoldsPolicyUpdateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_legal_holds_update_policy(id, name=name, description=description, members=members))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_linked_apps_list_member_linked_apps(
        self,
        team_member_id: str
    ) -> DropboxResponse:
        """List all linked applications of the team member. Note, this endpoint

        API Endpoint: /2/team/linked/apps_list_member_linked_apps
        Namespace: team
        Client type: team

        Args:
            team_member_id (str, required): Parameter for team_linked_apps_list_member_linked_apps

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            does not list any team-linked applications.
            Route attributes:
            scope: sessions.list
            :param str team_member_id: The team member id.
            :rtype: :class:`dropbox.team.ListMemberAppsResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.ListMemberAppsError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_linked_apps_list_member_linked_apps(team_member_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_linked_apps_list_members_linked_apps(
        self,
        cursor: Optional[str] = None
    ) -> DropboxResponse:
        """List all applications linked to the team members' accounts. Note, this

        API Endpoint: /2/team/linked/apps_list_members_linked_apps
        Namespace: team
        Client type: team

        Args:
            cursor (str, optional): Parameter for team_linked_apps_list_members_linked_apps

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            endpoint does not list any team-linked applications.
            Route attributes:
            scope: sessions.list
            :param Nullable[str] cursor: At the first call to the
            :meth:`team_linked_apps_list_members_linked_apps` the cursor
            shouldn't be passed. Then, if the result of the call includes a
            cursor, the following requests should include the received cursors
            in order to receive the next sub list of the team applications.
            :rtype: :class:`dropbox.team.ListMembersAppsResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.ListMembersAppsError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_linked_apps_list_members_linked_apps(cursor=cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_linked_apps_list_team_linked_apps(
        self,
        cursor: Optional[str] = None
    ) -> DropboxResponse:
        """List all applications linked to the team members' accounts. Note, this

        API Endpoint: /2/team/linked/apps_list_team_linked_apps
        Namespace: team
        Client type: team

        Args:
            cursor (str, optional): Parameter for team_linked_apps_list_team_linked_apps

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            endpoint doesn't list any team-linked applications.
            Route attributes:
            scope: sessions.list
            :param Nullable[str] cursor: At the first call to the
            :meth:`team_linked_apps_list_team_linked_apps` the cursor shouldn't
            be passed. Then, if the result of the call includes a cursor, the
            following requests should include the received cursors in order to
            receive the next sub list of the team applications.
            :rtype: :class:`dropbox.team.ListTeamAppsResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.ListTeamAppsError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_linked_apps_list_team_linked_apps(cursor=cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_linked_apps_revoke_linked_app(
        self,
        app_id: str,
        team_member_id: str,
        keep_app_folder: str = True
    ) -> DropboxResponse:
        """Revoke a linked application of the team member.

        API Endpoint: /2/team/linked/apps_revoke_linked_app
        Namespace: team
        Client type: team

        Args:
            app_id (str, required): Parameter for team_linked_apps_revoke_linked_app
            team_member_id (str, required): Parameter for team_linked_apps_revoke_linked_app
            keep_app_folder (str, optional): Parameter for team_linked_apps_revoke_linked_app

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sessions.modify
            :param str app_id: The application's unique id.
            :param str team_member_id: The unique id of the member owning the
            device.
            :param bool keep_app_folder: This flag is not longer supported, the
            application dedicated folder (in case the application uses  one)
            will be kept.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.RevokeLinkedAppError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_linked_apps_revoke_linked_app(app_id, team_member_id, keep_app_folder=keep_app_folder))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_linked_apps_revoke_linked_app_batch(
        self,
        revoke_linked_app: str
    ) -> DropboxResponse:
        """Revoke a list of linked applications of the team members.

        API Endpoint: /2/team/linked/apps_revoke_linked_app_batch
        Namespace: team
        Client type: team

        Args:
            revoke_linked_app (str, required): Parameter for team_linked_apps_revoke_linked_app_batch

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: sessions.modify
            :type revoke_linked_app:
            List[:class:`dropbox.team.RevokeLinkedApiAppArg`]
            :rtype: :class:`dropbox.team.RevokeLinkedAppBatchResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.RevokeLinkedAppBatchError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_linked_apps_revoke_linked_app_batch(revoke_linked_app))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_log_get_events(
        self,
        limit: str = 1000,
        account_id: Optional[str] = None,
        time: Optional[str] = None,
        category: Optional[str] = None,
        event_type: Optional[str] = None
    ) -> DropboxResponse:
        """Retrieves team events. If the result's ``GetTeamEventsResult.has_more``

        API Endpoint: /2/team/log/get_events
        Namespace: team
        Client type: team

        Args:
            limit (str, optional): Parameter for team_log_get_events
            account_id (str, optional): Parameter for team_log_get_events
            time (str, optional): Parameter for team_log_get_events
            category (str, optional): Parameter for team_log_get_events
            event_type (str, optional): Parameter for team_log_get_events

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            field is ``True``, call :meth:`team_log_get_events_continue` with the
            returned cursor to retrieve more entries. If end_time is not specified
            in your request, you may use the returned cursor to poll
            :meth:`team_log_get_events_continue` for new events. Many attributes
            note 'may be missing due to historical data gap'. Note that the
            file_operations category and & analogous paper events are not available
            on all Dropbox Business `plans </business/plans-comparison>`_. Use
            `features/get_values
            </developers/documentation/http/teams#team-features-get_values>`_ to
            check for this feature. Permission : Team Auditing.
            Route attributes:
            scope: events.read
            :param int limit: The maximal number of results to return per call. Note
            that some calls may not return ``limit`` number of events, and may
            even return no events, even with `has_more` set to true. In this
            case, callers should fetch again using
            :meth:`team_log_get_events_continue`.
            :param Nullable[str] account_id: Filter the events by account ID. Return
            only events with this account_id as either Actor, Context, or
            Participants.
            :param Nullable[:class:`dropbox.team_log.TimeRange`] time: Filter by
            time range.
            :param Nullable[:class:`dropbox.team_log.EventCategory`] category:
            Filter the returned events to a single category. Note that category
            shouldn't be provided together with event_type.
            :param Nullable[:class:`dropbox.team_log.EventTypeArg`] event_type:
            Filter the returned events to a single event type. Note that
            event_type shouldn't be provided together with category.
            :rtype: :class:`dropbox.team_log.GetTeamEventsResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team_log.GetTeamEventsError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_log_get_events(limit=limit, account_id=account_id, time=time, category=category, event_type=event_type))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_log_get_events_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from :meth:`team_log_get_events`, use

        API Endpoint: /2/team/log/get_events_continue
        Namespace: team
        Client type: team

        Args:
            cursor (str, required): Parameter for team_log_get_events_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to paginate through all events. Permission : Team Auditing.
            Route attributes:
            scope: events.read
            :param str cursor: Indicates from what point to get the next set of
            events.
            :rtype: :class:`dropbox.team_log.GetTeamEventsResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team_log.GetTeamEventsContinueError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_log_get_events_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_member_space_limits_excluded_users_add(
        self,
        users: Optional[str] = None
    ) -> DropboxResponse:
        """Add users to member space limits excluded users list.

        API Endpoint: /2/team/member/space_limits_excluded_users_add
        Namespace: team
        Client type: team

        Args:
            users (str, optional): Parameter for team_member_space_limits_excluded_users_add

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: members.write
            :param Nullable[List[:class:`dropbox.team.UserSelectorArg`]] users: List
            of users to be added/removed.
            :rtype: :class:`dropbox.team.ExcludedUsersUpdateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.ExcludedUsersUpdateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_member_space_limits_excluded_users_add(users=users))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_member_space_limits_excluded_users_list(
        self,
        limit: str = 1000
    ) -> DropboxResponse:
        """List member space limits excluded users.

        API Endpoint: /2/team/member/space_limits_excluded_users_list
        Namespace: team
        Client type: team

        Args:
            limit (str, optional): Parameter for team_member_space_limits_excluded_users_list

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: members.read
            :param int limit: Number of results to return per call.
            :rtype: :class:`dropbox.team.ExcludedUsersListResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.ExcludedUsersListError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_member_space_limits_excluded_users_list(limit=limit))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_member_space_limits_excluded_users_list_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Continue listing member space limits excluded users.

        API Endpoint: /2/team/member/space_limits_excluded_users_list_continue
        Namespace: team
        Client type: team

        Args:
            cursor (str, required): Parameter for team_member_space_limits_excluded_users_list_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: members.read
            :param str cursor: Indicates from what point to get the next set of
            users.
            :rtype: :class:`dropbox.team.ExcludedUsersListResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.ExcludedUsersListContinueError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_member_space_limits_excluded_users_list_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_member_space_limits_excluded_users_remove(
        self,
        users: Optional[str] = None
    ) -> DropboxResponse:
        """Remove users from member space limits excluded users list.

        API Endpoint: /2/team/member/space_limits_excluded_users_remove
        Namespace: team
        Client type: team

        Args:
            users (str, optional): Parameter for team_member_space_limits_excluded_users_remove

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: members.write
            :param Nullable[List[:class:`dropbox.team.UserSelectorArg`]] users: List
            of users to be added/removed.
            :rtype: :class:`dropbox.team.ExcludedUsersUpdateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.ExcludedUsersUpdateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_member_space_limits_excluded_users_remove(users=users))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_member_space_limits_get_custom_quota(
        self,
        users: str
    ) -> DropboxResponse:
        """Get users custom quota. A maximum of 1000 members can be specified in a

        API Endpoint: /2/team/member/space_limits_get_custom_quota
        Namespace: team
        Client type: team

        Args:
            users (str, required): Parameter for team_member_space_limits_get_custom_quota

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            single call. Note: to apply a custom space limit, a team admin needs to
            set a member space limit for the team first. (the team admin can check
            the settings here: https://www.dropbox.com/team/admin/settings/space).
            Route attributes:
            scope: members.read
            :param List[:class:`dropbox.team.UserSelectorArg`] users: List of users.
            :rtype: List[:class:`dropbox.team.CustomQuotaResult`]
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.CustomQuotaError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_member_space_limits_get_custom_quota(users))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_member_space_limits_remove_custom_quota(
        self,
        users: str
    ) -> DropboxResponse:
        """Remove users custom quota. A maximum of 1000 members can be specified in

        API Endpoint: /2/team/member/space_limits_remove_custom_quota
        Namespace: team
        Client type: team

        Args:
            users (str, required): Parameter for team_member_space_limits_remove_custom_quota

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            a single call. Note: to apply a custom space limit, a team admin needs
            to set a member space limit for the team first. (the team admin can
            check the settings here:
            https://www.dropbox.com/team/admin/settings/space).
            Route attributes:
            scope: members.write
            :param List[:class:`dropbox.team.UserSelectorArg`] users: List of users.
            :rtype: List[:class:`dropbox.team.RemoveCustomQuotaResult`]
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.CustomQuotaError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_member_space_limits_remove_custom_quota(users))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_member_space_limits_set_custom_quota(
        self,
        users_and_quotas: str
    ) -> DropboxResponse:
        """Set users custom quota. Custom quota has to be at least 15GB. A maximum

        API Endpoint: /2/team/member/space_limits_set_custom_quota
        Namespace: team
        Client type: team

        Args:
            users_and_quotas (str, required): Parameter for team_member_space_limits_set_custom_quota

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            of 1000 members can be specified in a single call. Note: to apply a
            custom space limit, a team admin needs to set a member space limit for
            the team first. (the team admin can check the settings here:
            https://www.dropbox.com/team/admin/settings/space).
            Route attributes:
            scope: members.read
            :param List[:class:`dropbox.team.UserCustomQuotaArg`] users_and_quotas:
            List of users and their custom quotas.
            :rtype: List[:class:`dropbox.team.CustomQuotaResult`]
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.SetCustomQuotaError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_member_space_limits_set_custom_quota(users_and_quotas))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_add(
        self,
        new_members: str,
        force_async: str = False
    ) -> DropboxResponse:
        """Adds members to a team. Permission : Team member management A maximum of

        API Endpoint: /2/team/members/add
        Namespace: team
        Client type: team

        Args:
            new_members (str, required): Parameter for team_members_add
            force_async (str, optional): Parameter for team_members_add

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            20 members can be specified in a single call. If no Dropbox account
            exists with the email address specified, a new Dropbox account will be
            created with the given email address, and that account will be invited
            to the team. If a personal Dropbox account exists with the email address
            specified in the call, this call will create a placeholder Dropbox
            account for the user on the team and send an email inviting the user to
            migrate their existing personal account onto the team. Team member
            management apps are required to set an initial given_name and surname
            for a user to use in the team invitation and for 'Perform as team
            member' actions taken on the user before they become 'active'.
            Route attributes:
            scope: members.write
            :param List[:class:`dropbox.team.MemberAddArg`] new_members: Details of
            new members to be added to the team.
            :rtype: :class:`dropbox.team.MembersAddLaunch`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_add(new_members, force_async=force_async))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_add_job_status_get(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Once an async_job_id is returned from :meth:`team_members_add` , use

        API Endpoint: /2/team/members/add_job_status_get
        Namespace: team
        Client type: team

        Args:
            async_job_id (str, required): Parameter for team_members_add_job_status_get

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to poll the status of the asynchronous request. Permission : Team
            member management.
            Route attributes:
            scope: members.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.team.MembersAddJobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.PollError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_add_job_status_get(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_add_job_status_get_v2(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Once an async_job_id is returned from :meth:`team_members_add_v2` , use

        API Endpoint: /2/team/members/add_job_status_get_v2
        Namespace: team
        Client type: team

        Args:
            async_job_id (str, required): Parameter for team_members_add_job_status_get_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to poll the status of the asynchronous request. Permission : Team
            member management.
            Route attributes:
            scope: members.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.team.MembersAddJobStatusV2Result`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.PollError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_add_job_status_get_v2(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_add_v2(
        self,
        new_members: str,
        force_async: str = False
    ) -> DropboxResponse:
        """Adds members to a team. Permission : Team member management A maximum of

        API Endpoint: /2/team/members/add_v2
        Namespace: team
        Client type: team

        Args:
            new_members (str, required): Parameter for team_members_add_v2
            force_async (str, optional): Parameter for team_members_add_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            20 members can be specified in a single call. If no Dropbox account
            exists with the email address specified, a new Dropbox account will be
            created with the given email address, and that account will be invited
            to the team. If a personal Dropbox account exists with the email address
            specified in the call, this call will create a placeholder Dropbox
            account for the user on the team and send an email inviting the user to
            migrate their existing personal account onto the team. Team member
            management apps are required to set an initial given_name and surname
            for a user to use in the team invitation and for 'Perform as team
            member' actions taken on the user before they become 'active'.
            Route attributes:
            scope: members.write
            :param List[:class:`dropbox.team.MemberAddV2Arg`] new_members: Details
            of new members to be added to the team.
            :rtype: :class:`dropbox.team.MembersAddLaunchV2Result`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_add_v2(new_members, force_async=force_async))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_delete_profile_photo(
        self,
        user: str
    ) -> DropboxResponse:
        """Deletes a team member's profile photo. Permission : Team member

        API Endpoint: /2/team/members/delete_profile_photo
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_delete_profile_photo

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            management.
            Route attributes:
            scope: members.write
            :param user: Identity of the user whose profile photo will be deleted.
            :type user: :class:`dropbox.team.UserSelectorArg`
            :rtype: :class:`dropbox.team.TeamMemberInfo`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersDeleteProfilePhotoError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_delete_profile_photo(user))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_delete_profile_photo_v2(
        self,
        user: str
    ) -> DropboxResponse:
        """Deletes a team member's profile photo. Permission : Team member

        API Endpoint: /2/team/members/delete_profile_photo_v2
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_delete_profile_photo_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            management.
            Route attributes:
            scope: members.write
            :param user: Identity of the user whose profile photo will be deleted.
            :type user: :class:`dropbox.team.UserSelectorArg`
            :rtype: :class:`dropbox.team.TeamMemberInfoV2Result`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersDeleteProfilePhotoError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_delete_profile_photo_v2(user))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_get_available_team_member_roles(self) -> DropboxResponse:
        """Get available TeamMemberRoles for the connected team. To be used with

        API Endpoint: /2/team/members/get_available_team_member_roles
        Namespace: team
        Client type: team

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`team_members_set_admin_permissions_v2`. Permission : Team member
            management.
            Route attributes:
            scope: members.read
            :rtype: :class:`dropbox.team.MembersGetAvailableTeamMemberRolesResult`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_get_available_team_member_roles())
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_get_info(
        self,
        members: str
    ) -> DropboxResponse:
        """Returns information about multiple team members. Permission : Team

        API Endpoint: /2/team/members/get_info
        Namespace: team
        Client type: team

        Args:
            members (str, required): Parameter for team_members_get_info

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            information This endpoint will return
            ``MembersGetInfoItem.id_not_found``, for IDs (or emails) that cannot be
            matched to a valid team member.
            Route attributes:
            scope: members.read
            :param List[:class:`dropbox.team.UserSelectorArg`] members: List of team
            members.
            :rtype: List[:class:`dropbox.team.MembersGetInfoItem`]
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersGetInfoError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_get_info(members))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_get_info_v2(
        self,
        members: List[UserSelectorArg]
    ) -> DropboxResponse:
        """Returns information about multiple team members. Permission : Team

        API Endpoint: /2/team/members/get_info_v2
        Namespace: team
        Client type: team

        Args:
            members (str, required): Parameter for team_members_get_info_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            information This endpoint will return
            ``MembersGetInfoItem.id_not_found``, for IDs (or emails) that cannot be
            matched to a valid team member.
            Route attributes:
            scope: members.read
            :param List[:class:`dropbox.team.UserSelectorArg`] members: List of team
            members.
            :rtype: :class:`dropbox.team.MembersGetInfoV2Result`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersGetInfoError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_get_info_v2(members))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_list(
        self,
        limit: str = 1000,
        include_removed: str = False
    ) -> DropboxResponse:
        """Lists members of a team. Permission : Team information.

        API Endpoint: /2/team/members/list
        Namespace: team
        Client type: team

        Args:
            limit (str, optional): Parameter for team_members_list
            include_removed (str, optional): Parameter for team_members_list

        Returns:
            DropboxResponse: SDK response

        Returns Model:
            List[User]

        Original SDK Documentation:
            Route attributes:
            scope: members.read
            :param int limit: Number of results to return per call.
            :param bool include_removed: Whether to return removed members.
            :rtype: :class:`dropbox.team.MembersListResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersListError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.team_members_list(limit=limit, include_removed=include_removed)
            )
            return DropboxResponse(success=True, data=response)

        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_list_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from :meth:`team_members_list`, use

        API Endpoint: /2/team/members/list_continue
        Namespace: team
        Client type: team

        Args:
            cursor (str, required): Parameter for team_members_list_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to paginate through all team members. Permission : Team
            information.
            Route attributes:
            scope: members.read
            :param str cursor: Indicates from what point to get the next set of
            members.
            :rtype: :class:`dropbox.team.MembersListResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersListContinueError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_list_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_list_continue_v2(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from :meth:`team_members_list_v2`, use

        API Endpoint: /2/team/members/list_continue_v2
        Namespace: team
        Client type: team

        Args:
            cursor (str, required): Parameter for team_members_list_continue_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to paginate through all team members. Permission : Team
            information.
            Route attributes:
            scope: members.read
            :param str cursor: Indicates from what point to get the next set of
            members.
            :rtype: :class:`dropbox.team.MembersListV2Result`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersListContinueError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_list_continue_v2(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_list_v2(
        self,
        limit: str = 1000,
        include_removed: str = False
    ) -> DropboxResponse:
        """Lists members of a team. Permission : Team information.

        API Endpoint: /2/team/members/list_v2
        Namespace: team
        Client type: team

        Args:
            limit (str, optional): Parameter for team_members_list_v2
            include_removed (str, optional): Parameter for team_members_list_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: members.read
            :param int limit: Number of results to return per call.
            :param bool include_removed: Whether to return removed members.
            :rtype: :class:`dropbox.team.MembersListV2Result`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersListError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_list_v2(limit=limit, include_removed=include_removed))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_move_former_member_files(
        self,
        user: str,
        transfer_dest_id: str,
        transfer_admin_id: str
    ) -> DropboxResponse:
        """Moves removed member's files to a different member. This endpoint

        API Endpoint: /2/team/members/move_former_member_files
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_move_former_member_files
            transfer_dest_id (str, required): Parameter for team_members_move_former_member_files
            transfer_admin_id (str, required): Parameter for team_members_move_former_member_files

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            initiates an asynchronous job. To obtain the final result of the job,
            the client should periodically poll
            :meth:`team_members_move_former_member_files_job_status_check`.
            Permission : Team member management.
            Route attributes:
            scope: members.write
            :param transfer_dest_id: Files from the deleted member account will be
            transferred to this user.
            :type transfer_dest_id: :class:`dropbox.team.UserSelectorArg`
            :param transfer_admin_id: Errors during the transfer process will be
            sent via email to this user.
            :type transfer_admin_id: :class:`dropbox.team.UserSelectorArg`
            :rtype: :class:`dropbox.team.LaunchEmptyResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersTransferFormerMembersFilesError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_move_former_member_files(user, transfer_dest_id, transfer_admin_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_move_former_member_files_job_status_check(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Once an async_job_id is returned from

        API Endpoint: /2/team/members/move_former_member_files_job_status_check
        Namespace: team
        Client type: team

        Args:
            async_job_id (str, required): Parameter for team_members_move_former_member_files_job_status_check

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            :meth:`team_members_move_former_member_files` , use this to poll the
            status of the asynchronous request. Permission : Team member management.
            Route attributes:
            scope: members.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.team.PollEmptyResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.PollError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_move_former_member_files_job_status_check(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_recover(
        self,
        user: str
    ) -> DropboxResponse:
        """Recover a deleted member. Permission : Team member management Exactly

        API Endpoint: /2/team/members/recover
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_recover

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            one of team_member_id, email, or external_id must be provided to
            identify the user account.
            Route attributes:
            scope: members.delete
            :param user: Identity of user to recover.
            :type user: :class:`dropbox.team.UserSelectorArg`
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersRecoverError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_recover(user))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_remove(
        self,
        user: str,
        wipe_data: str = True,
        transfer_dest_id: Optional[str] = None,
        transfer_admin_id: Optional[str] = None,
        keep_account: str = False,
        retain_team_shares: str = False
    ) -> DropboxResponse:
        """Removes a member from a team. Permission : Team member management

        API Endpoint: /2/team/members/remove
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_remove
            wipe_data (str, optional): Parameter for team_members_remove
            transfer_dest_id (str, optional): Parameter for team_members_remove
            transfer_admin_id (str, optional): Parameter for team_members_remove
            keep_account (str, optional): Parameter for team_members_remove
            retain_team_shares (str, optional): Parameter for team_members_remove

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Exactly one of team_member_id, email, or external_id must be provided to
            identify the user account. Accounts can be recovered via
            :meth:`team_members_recover` for a 7 day period or until the account has
            been permanently deleted or transferred to another account (whichever
            comes first). Calling :meth:`team_members_add` while a user is still
            recoverable on your team will return with
            ``MemberAddResult.user_already_on_team``. Accounts can have their files
            transferred via the admin console for a limited time, based on the
            version history length associated with the team (180 days for most
            teams). This endpoint may initiate an asynchronous job. To obtain the
            final result of the job, the client should periodically poll
            :meth:`team_members_remove_job_status_get`.
            Route attributes:
            scope: members.delete
            :param Nullable[:class:`dropbox.team.UserSelectorArg`] transfer_dest_id:
            If provided, files from the deleted member account will be
            transferred to this user.
            :param Nullable[:class:`dropbox.team.UserSelectorArg`]
            transfer_admin_id: If provided, errors during the transfer process
            will be sent via email to this user. If the transfer_dest_id
            argument was provided, then this argument must be provided as well.
            :param bool keep_account: Downgrade the member to a Basic account. The
            user will retain the email address associated with their Dropbox
            account and data in their account that is not restricted to team
            members. In order to keep the account the argument ``wipe_data``
            should be set to ``False``.
            :param bool retain_team_shares: If provided, allows removed users to
            keep access to Dropbox folders (not Dropbox Paper folders) already
            explicitly shared with them (not via a group) when they are
            downgraded to a Basic account. Users will not retain access to
            folders that do not allow external sharing. In order to keep the
            sharing relationships, the arguments ``wipe_data`` should be set to
            ``False`` and ``keep_account`` should be set to ``True``.
            :rtype: :class:`dropbox.team.LaunchEmptyResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersRemoveError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_remove(user, wipe_data=wipe_data, transfer_dest_id=transfer_dest_id, transfer_admin_id=transfer_admin_id, keep_account=keep_account, retain_team_shares=retain_team_shares))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_remove_job_status_get(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Once an async_job_id is returned from :meth:`team_members_remove` , use

        API Endpoint: /2/team/members/remove_job_status_get
        Namespace: team
        Client type: team

        Args:
            async_job_id (str, required): Parameter for team_members_remove_job_status_get

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to poll the status of the asynchronous request. Permission : Team
            member management.
            Route attributes:
            scope: members.delete
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.team.PollEmptyResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.PollError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_remove_job_status_get(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_secondary_emails_add(
        self,
        new_secondary_emails: str
    ) -> DropboxResponse:
        """Add secondary emails to users. Permission : Team member management.

        API Endpoint: /2/team/members/secondary_emails_add
        Namespace: team
        Client type: team

        Args:
            new_secondary_emails (str, required): Parameter for team_members_secondary_emails_add

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Emails that are on verified domains will be verified automatically. For
            each email address not on a verified domain a verification email will be
            sent.
            Route attributes:
            scope: members.write
            :param List[:class:`dropbox.team.UserSecondaryEmailsArg`]
            new_secondary_emails: List of users and secondary emails to add.
            :rtype: :class:`dropbox.team.AddSecondaryEmailsResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.AddSecondaryEmailsError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_secondary_emails_add(new_secondary_emails))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_secondary_emails_delete(
        self,
        emails_to_delete: str
    ) -> DropboxResponse:
        """Delete secondary emails from users Permission : Team member management.

        API Endpoint: /2/team/members/secondary_emails_delete
        Namespace: team
        Client type: team

        Args:
            emails_to_delete (str, required): Parameter for team_members_secondary_emails_delete

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Users will be notified of deletions of verified secondary emails at both
            the secondary email and their primary email.
            Route attributes:
            scope: members.write
            :param List[:class:`dropbox.team.UserSecondaryEmailsArg`]
            emails_to_delete: List of users and their secondary emails to
            delete.
            :rtype: :class:`dropbox.team.DeleteSecondaryEmailsResult`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_secondary_emails_delete(emails_to_delete))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_secondary_emails_resend_verification_emails(
        self,
        emails_to_resend: str
    ) -> DropboxResponse:
        """Resend secondary email verification emails. Permission : Team member

        API Endpoint: /2/team/members/secondary_emails_resend_verification_emails
        Namespace: team
        Client type: team

        Args:
            emails_to_resend (str, required): Parameter for team_members_secondary_emails_resend_verification_emails

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            management.
            Route attributes:
            scope: members.write
            :param List[:class:`dropbox.team.UserSecondaryEmailsArg`]
            emails_to_resend: List of users and secondary emails to resend
            verification emails to.
            :rtype: :class:`dropbox.team.ResendVerificationEmailResult`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_secondary_emails_resend_verification_emails(emails_to_resend))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_send_welcome_email(
        self,
        arg: str
    ) -> DropboxResponse:
        """Sends welcome email to pending team member. Permission : Team member

        API Endpoint: /2/team/members/send_welcome_email
        Namespace: team
        Client type: team

        Args:
            arg (str, required): Parameter for team_members_send_welcome_email

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            management Exactly one of team_member_id, email, or external_id must be
            provided to identify the user account. No-op if team member is not
            pending.
            Route attributes:
            scope: members.write
            :param arg: Argument for selecting a single user, either by
            team_member_id, external_id or email.
            :type arg: :class:`dropbox.team.UserSelectorArg`
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersSendWelcomeError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_send_welcome_email(arg))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_set_admin_permissions(
        self,
        user: str,
        new_role: str
    ) -> DropboxResponse:
        """Updates a team member's permissions. Permission : Team member

        API Endpoint: /2/team/members/set_admin_permissions
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_set_admin_permissions
            new_role (str, required): Parameter for team_members_set_admin_permissions

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            management.
            Route attributes:
            scope: members.write
            :param user: Identity of user whose role will be set.
            :type user: :class:`dropbox.team.UserSelectorArg`
            :param new_role: The new role of the member.
            :type new_role: :class:`dropbox.team.AdminTier`
            :rtype: :class:`dropbox.team.MembersSetPermissionsResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersSetPermissionsError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_set_admin_permissions(user, new_role))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_set_admin_permissions_v2(
        self,
        user: str,
        new_roles: Optional[str] = None
    ) -> DropboxResponse:
        """Updates a team member's permissions. Permission : Team member

        API Endpoint: /2/team/members/set_admin_permissions_v2
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_set_admin_permissions_v2
            new_roles (str, optional): Parameter for team_members_set_admin_permissions_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            management.
            Route attributes:
            scope: members.write
            :param user: Identity of user whose role will be set.
            :type user: :class:`dropbox.team.UserSelectorArg`
            :param Nullable[List[str]] new_roles: The new roles for the member. Send
            empty list to make user member only. For now, only up to one role is
            allowed.
            :rtype: :class:`dropbox.team.MembersSetPermissions2Result`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersSetPermissions2Error`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_set_admin_permissions_v2(user, new_roles=new_roles))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_set_profile(
        self,
        user: str,
        new_email: Optional[str] = None,
        new_external_id: Optional[str] = None,
        new_given_name: Optional[str] = None,
        new_surname: Optional[str] = None,
        new_persistent_id: Optional[str] = None,
        new_is_directory_restricted: Optional[str] = None
    ) -> DropboxResponse:
        """Updates a team member's profile. Permission : Team member management.

        API Endpoint: /2/team/members/set_profile
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_set_profile
            new_email (str, optional): Parameter for team_members_set_profile
            new_external_id (str, optional): Parameter for team_members_set_profile
            new_given_name (str, optional): Parameter for team_members_set_profile
            new_surname (str, optional): Parameter for team_members_set_profile
            new_persistent_id (str, optional): Parameter for team_members_set_profile
            new_is_directory_restricted (str, optional): Parameter for team_members_set_profile

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: members.write
            :param user: Identity of user whose profile will be set.
            :type user: :class:`dropbox.team.UserSelectorArg`
            :param Nullable[str] new_email: New email for member.
            :param Nullable[str] new_external_id: New external ID for member.
            :param Nullable[str] new_given_name: New given name for member.
            :param Nullable[str] new_surname: New surname for member.
            :param Nullable[str] new_persistent_id: New persistent ID. This field
            only available to teams using persistent ID SAML configuration.
            :param Nullable[bool] new_is_directory_restricted: New value for whether
            the user is a directory restricted user.
            :rtype: :class:`dropbox.team.TeamMemberInfo`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersSetProfileError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_set_profile(user, new_email=new_email, new_external_id=new_external_id, new_given_name=new_given_name, new_surname=new_surname, new_persistent_id=new_persistent_id, new_is_directory_restricted=new_is_directory_restricted))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_set_profile_photo(
        self,
        user: str,
        photo: str
    ) -> DropboxResponse:
        """Updates a team member's profile photo. Permission : Team member

        API Endpoint: /2/team/members/set_profile_photo
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_set_profile_photo
            photo (str, required): Parameter for team_members_set_profile_photo

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            management.
            Route attributes:
            scope: members.write
            :param user: Identity of the user whose profile photo will be set.
            :type user: :class:`dropbox.team.UserSelectorArg`
            :param photo: Image to set as the member's new profile photo.
            :type photo: :class:`dropbox.team.PhotoSourceArg`
            :rtype: :class:`dropbox.team.TeamMemberInfo`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersSetProfilePhotoError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_set_profile_photo(user, photo))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_set_profile_photo_v2(
        self,
        user: str,
        photo: str
    ) -> DropboxResponse:
        """Updates a team member's profile photo. Permission : Team member

        API Endpoint: /2/team/members/set_profile_photo_v2
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_set_profile_photo_v2
            photo (str, required): Parameter for team_members_set_profile_photo_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            management.
            Route attributes:
            scope: members.write
            :param user: Identity of the user whose profile photo will be set.
            :type user: :class:`dropbox.team.UserSelectorArg`
            :param photo: Image to set as the member's new profile photo.
            :type photo: :class:`dropbox.team.PhotoSourceArg`
            :rtype: :class:`dropbox.team.TeamMemberInfoV2Result`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersSetProfilePhotoError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_set_profile_photo_v2(user, photo))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_set_profile_v2(
        self,
        user: str,
        new_email: Optional[str] = None,
        new_external_id: Optional[str] = None,
        new_given_name: Optional[str] = None,
        new_surname: Optional[str] = None,
        new_persistent_id: Optional[str] = None,
        new_is_directory_restricted: Optional[str] = None
    ) -> DropboxResponse:
        """Updates a team member's profile. Permission : Team member management.

        API Endpoint: /2/team/members/set_profile_v2
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_set_profile_v2
            new_email (str, optional): Parameter for team_members_set_profile_v2
            new_external_id (str, optional): Parameter for team_members_set_profile_v2
            new_given_name (str, optional): Parameter for team_members_set_profile_v2
            new_surname (str, optional): Parameter for team_members_set_profile_v2
            new_persistent_id (str, optional): Parameter for team_members_set_profile_v2
            new_is_directory_restricted (str, optional): Parameter for team_members_set_profile_v2

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: members.write
            :param user: Identity of user whose profile will be set.
            :type user: :class:`dropbox.team.UserSelectorArg`
            :param Nullable[str] new_email: New email for member.
            :param Nullable[str] new_external_id: New external ID for member.
            :param Nullable[str] new_given_name: New given name for member.
            :param Nullable[str] new_surname: New surname for member.
            :param Nullable[str] new_persistent_id: New persistent ID. This field
            only available to teams using persistent ID SAML configuration.
            :param Nullable[bool] new_is_directory_restricted: New value for whether
            the user is a directory restricted user.
            :rtype: :class:`dropbox.team.TeamMemberInfoV2Result`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersSetProfileError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_set_profile_v2(user, new_email=new_email, new_external_id=new_external_id, new_given_name=new_given_name, new_surname=new_surname, new_persistent_id=new_persistent_id, new_is_directory_restricted=new_is_directory_restricted))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_suspend(
        self,
        user: str,
        wipe_data: str = True
    ) -> DropboxResponse:
        """Suspend a member from a team. Permission : Team member management

        API Endpoint: /2/team/members/suspend
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_suspend
            wipe_data (str, optional): Parameter for team_members_suspend

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Exactly one of team_member_id, email, or external_id must be provided to
            identify the user account.
            Route attributes:
            scope: members.write
            :param bool wipe_data: If provided, controls if the user's data will be
            deleted on their linked devices.
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersSuspendError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_suspend(user, wipe_data=wipe_data))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_members_unsuspend(
        self,
        user: str
    ) -> DropboxResponse:
        """Unsuspend a member from a team. Permission : Team member management

        API Endpoint: /2/team/members/unsuspend
        Namespace: team
        Client type: team

        Args:
            user (str, required): Parameter for team_members_unsuspend

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Exactly one of team_member_id, email, or external_id must be provided to
            identify the user account.
            Route attributes:
            scope: members.write
            :param user: Identity of user to unsuspend.
            :type user: :class:`dropbox.team.UserSelectorArg`
            :rtype: None
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.MembersUnsuspendError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_members_unsuspend(user))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_namespaces_list(
        self,
        limit: str = 1000
    ) -> DropboxResponse:
        """Returns a list of all team-accessible namespaces. This list includes

        API Endpoint: /2/team/namespaces/list
        Namespace: team
        Client type: team

        Args:
            limit (str, optional): Parameter for team_namespaces_list

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            team folders, shared folders containing team members, team members' home
            namespaces, and team members' app folders. Home namespaces and app
            folders are always owned by this team or members of the team, but shared
            folders may be owned by other users or other teams. Duplicates may occur
            in the list.
            Route attributes:
            scope: team_data.member
            :param int limit: Specifying a value here has no effect.
            :rtype: :class:`dropbox.team.TeamNamespacesListResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.TeamNamespacesListError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_namespaces_list(limit=limit))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_namespaces_list_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from :meth:`team_namespaces_list`, use

        API Endpoint: /2/team/namespaces/list_continue
        Namespace: team
        Client type: team

        Args:
            cursor (str, required): Parameter for team_namespaces_list_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to paginate through all team-accessible namespaces. Duplicates may
            occur in the list.
            Route attributes:
            scope: team_data.member
            :param str cursor: Indicates from what point to get the next set of
            team-accessible namespaces.
            :rtype: :class:`dropbox.team.TeamNamespacesListResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.TeamNamespacesListContinueError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_namespaces_list_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_properties_template_add(
        self,
        name: str,
        description: str,
        fields: str
    ) -> DropboxResponse:
        """Permission : Team member file access.

        API Endpoint: /2/team/properties/template_add
        Namespace: team
        Client type: team

        Args:
            name (str, required): Parameter for team_properties_template_add
            description (str, required): Parameter for team_properties_template_add
            fields (str, required): Parameter for team_properties_template_add

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.team_metadata.write
            :rtype: :class:`dropbox.team.AddTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.ModifyTemplateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_properties_template_add(name, description, fields))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_properties_template_get(
        self,
        template_id: str
    ) -> DropboxResponse:
        """Permission : Team member file access. The scope for the route is

        API Endpoint: /2/team/properties/template_get
        Namespace: team
        Client type: team

        Args:
            template_id (str, required): Parameter for team_properties_template_get

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            files.team_metadata.write.
            Route attributes:
            scope: files.team_metadata.write
            :param str template_id: An identifier for template added by route  See
            :meth:`team_templates_add_for_user` or
            :meth:`team_templates_add_for_team`.
            :rtype: :class:`dropbox.team.GetTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.TemplateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_properties_template_get(template_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_properties_template_list(self) -> DropboxResponse:
        """Permission : Team member file access. The scope for the route is

        API Endpoint: /2/team/properties/template_list
        Namespace: team
        Client type: team

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            files.team_metadata.write.
            Route attributes:
            scope: files.team_metadata.write
            :rtype: :class:`dropbox.team.ListTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.TemplateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_properties_template_list())
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_properties_template_update(
        self,
        template_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        add_fields: Optional[str] = None
    ) -> DropboxResponse:
        """Permission : Team member file access.

        API Endpoint: /2/team/properties/template_update
        Namespace: team
        Client type: team

        Args:
            template_id (str, required): Parameter for team_properties_template_update
            name (str, optional): Parameter for team_properties_template_update
            description (str, optional): Parameter for team_properties_template_update
            add_fields (str, optional): Parameter for team_properties_template_update

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: files.team_metadata.write
            :param str template_id: An identifier for template added by  See
            :meth:`team_templates_add_for_user` or
            :meth:`team_templates_add_for_team`.
            :param Nullable[str] name: A display name for the template. template
            names can be up to 256 bytes.
            :param Nullable[str] description: Description for the new template.
            Template descriptions can be up to 1024 bytes.
            :param Nullable[List[:class:`dropbox.team.PropertyFieldTemplate`]]
            add_fields: Property field templates to be added to the group
            template. There can be up to 32 properties in a single template.
            :rtype: :class:`dropbox.team.UpdateTemplateResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.ModifyTemplateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_properties_template_update(template_id, name=name, description=description, add_fields=add_fields))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_reports_get_activity(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> DropboxResponse:
        """Retrieves reporting data about a team's user activity. Deprecated: Will

        API Endpoint: /2/team/reports/get_activity
        Namespace: team
        Client type: team

        Args:
            start_date (str, optional): Parameter for team_reports_get_activity
            end_date (str, optional): Parameter for team_reports_get_activity

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            be removed on July 1st 2021.
            Route attributes:
            scope: team_info.read
            :param Nullable[datetime] start_date: Optional starting date
            (inclusive). If start_date is None or too long ago, this field will
            be set to 6 months ago.
            :param Nullable[datetime] end_date: Optional ending date (exclusive).
            :rtype: :class:`dropbox.team.GetActivityReport`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.DateRangeError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_reports_get_activity(start_date=start_date, end_date=end_date))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_reports_get_devices(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> DropboxResponse:
        """Retrieves reporting data about a team's linked devices. Deprecated: Will

        API Endpoint: /2/team/reports/get_devices
        Namespace: team
        Client type: team

        Args:
            start_date (str, optional): Parameter for team_reports_get_devices
            end_date (str, optional): Parameter for team_reports_get_devices

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            be removed on July 1st 2021.
            Route attributes:
            scope: team_info.read
            :param Nullable[datetime] start_date: Optional starting date
            (inclusive). If start_date is None or too long ago, this field will
            be set to 6 months ago.
            :param Nullable[datetime] end_date: Optional ending date (exclusive).
            :rtype: :class:`dropbox.team.GetDevicesReport`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.DateRangeError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_reports_get_devices(start_date=start_date, end_date=end_date))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_reports_get_membership(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> DropboxResponse:
        """Retrieves reporting data about a team's membership. Deprecated: Will be

        API Endpoint: /2/team/reports/get_membership
        Namespace: team
        Client type: team

        Args:
            start_date (str, optional): Parameter for team_reports_get_membership
            end_date (str, optional): Parameter for team_reports_get_membership

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            removed on July 1st 2021.
            Route attributes:
            scope: team_info.read
            :param Nullable[datetime] start_date: Optional starting date
            (inclusive). If start_date is None or too long ago, this field will
            be set to 6 months ago.
            :param Nullable[datetime] end_date: Optional ending date (exclusive).
            :rtype: :class:`dropbox.team.GetMembershipReport`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.DateRangeError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_reports_get_membership(start_date=start_date, end_date=end_date))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_reports_get_storage(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> DropboxResponse:
        """Retrieves reporting data about a team's storage usage. Deprecated: Will

        API Endpoint: /2/team/reports/get_storage
        Namespace: team
        Client type: team

        Args:
            start_date (str, optional): Parameter for team_reports_get_storage
            end_date (str, optional): Parameter for team_reports_get_storage

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            be removed on July 1st 2021.
            Route attributes:
            scope: team_info.read
            :param Nullable[datetime] start_date: Optional starting date
            (inclusive). If start_date is None or too long ago, this field will
            be set to 6 months ago.
            :param Nullable[datetime] end_date: Optional ending date (exclusive).
            :rtype: :class:`dropbox.team.GetStorageReport`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.DateRangeError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_reports_get_storage(start_date=start_date, end_date=end_date))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_sharing_allowlist_add(
        self,
        domains: Optional[str] = None,
        emails: Optional[str] = None
    ) -> DropboxResponse:
        """Endpoint adds Approve List entries. Changes are effective immediately.

        API Endpoint: /2/team/sharing/allowlist_add
        Namespace: team
        Client type: team

        Args:
            domains (str, optional): Parameter for team_sharing_allowlist_add
            emails (str, optional): Parameter for team_sharing_allowlist_add

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Changes are committed in transaction. In case of single validation error
            - all entries are rejected. Valid domains (RFC-1034/5) and emails
            (RFC-5322/822) are accepted. Added entries cannot overflow limit of
            10000 entries per team. Maximum 100 entries per call is allowed.
            Route attributes:
            scope: team_info.write
            :param Nullable[List[str]] domains: List of domains represented by valid
            string representation (RFC-1034/5).
            :param Nullable[List[str]] emails: List of emails represented by valid
            string representation (RFC-5322/822).
            :rtype: :class:`dropbox.team.SharingAllowlistAddResponse`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.SharingAllowlistAddError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_sharing_allowlist_add(domains=domains, emails=emails))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_sharing_allowlist_list(
        self,
        limit: str = 1000
    ) -> DropboxResponse:
        """Lists Approve List entries for given team, from newest to oldest,

        API Endpoint: /2/team/sharing/allowlist_list
        Namespace: team
        Client type: team

        Args:
            limit (str, optional): Parameter for team_sharing_allowlist_list

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            returning up to `limit` entries at a time. If there are more than
            `limit` entries associated with the current team, more can be fetched by
            passing the returned `cursor` to
            :meth:`team_sharing_allowlist_list_continue`.
            Route attributes:
            scope: team_info.read
            :param int limit: The number of entries to fetch at one time.
            :rtype: :class:`dropbox.team.SharingAllowlistListResponse`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_sharing_allowlist_list(limit=limit))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_sharing_allowlist_list_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Lists entries associated with given team, starting from a the cursor.

        API Endpoint: /2/team/sharing/allowlist_list_continue
        Namespace: team
        Client type: team

        Args:
            cursor (str, required): Parameter for team_sharing_allowlist_list_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            See :meth:`team_sharing_allowlist_list`.
            Route attributes:
            scope: team_info.read
            :param str cursor: The cursor returned from a previous call to
            :meth:`team_sharing_allowlist_list` or
            :meth:`team_sharing_allowlist_list_continue`.
            :rtype: :class:`dropbox.team.SharingAllowlistListResponse`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.SharingAllowlistListContinueError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_sharing_allowlist_list_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_sharing_allowlist_remove(
        self,
        domains: Optional[str] = None,
        emails: Optional[str] = None
    ) -> DropboxResponse:
        """Endpoint removes Approve List entries. Changes are effective

        API Endpoint: /2/team/sharing/allowlist_remove
        Namespace: team
        Client type: team

        Args:
            domains (str, optional): Parameter for team_sharing_allowlist_remove
            emails (str, optional): Parameter for team_sharing_allowlist_remove

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            immediately. Changes are committed in transaction. In case of single
            validation error - all entries are rejected. Valid domains (RFC-1034/5)
            and emails (RFC-5322/822) are accepted. Entries being removed have to be
            present on the list. Maximum 1000 entries per call is allowed.
            Route attributes:
            scope: team_info.write
            :param Nullable[List[str]] domains: List of domains represented by valid
            string representation (RFC-1034/5).
            :param Nullable[List[str]] emails: List of emails represented by valid
            string representation (RFC-5322/822).
            :rtype: :class:`dropbox.team.SharingAllowlistRemoveResponse`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.SharingAllowlistRemoveError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_sharing_allowlist_remove(domains=domains, emails=emails))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_team_folder_activate(
        self,
        team_folder_id: str
    ) -> DropboxResponse:
        """Sets an archived team folder's status to active. Permission : Team

        API Endpoint: /2/team/team/folder_activate
        Namespace: team
        Client type: team

        Args:
            team_folder_id (str, required): Parameter for team_team_folder_activate

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            member file access.
            Route attributes:
            scope: team_data.content.write
            :param str team_folder_id: The ID of the team folder.
            :rtype: :class:`dropbox.team.TeamFolderMetadata`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_folder_activate(team_folder_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_team_folder_archive(
        self,
        team_folder_id: str,
        force_async_off: str = False
    ) -> DropboxResponse:
        """Sets an active team folder's status to archived and removes all folder

        API Endpoint: /2/team/team/folder_archive
        Namespace: team
        Client type: team

        Args:
            team_folder_id (str, required): Parameter for team_team_folder_archive
            force_async_off (str, optional): Parameter for team_team_folder_archive

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            and file members. This endpoint cannot be used for teams that have a
            shared team space. Permission : Team member file access.
            Route attributes:
            scope: team_data.content.write
            :param bool force_async_off: Whether to force the archive to happen
            synchronously.
            :rtype: :class:`dropbox.team.TeamFolderArchiveLaunch`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_folder_archive(team_folder_id, force_async_off=force_async_off))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_team_folder_archive_check(
        self,
        async_job_id: str
    ) -> DropboxResponse:
        """Returns the status of an asynchronous job for archiving a team folder.

        API Endpoint: /2/team/team/folder_archive_check
        Namespace: team
        Client type: team

        Args:
            async_job_id (str, required): Parameter for team_team_folder_archive_check

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Permission : Team member file access.
            Route attributes:
            scope: team_data.content.write
            :param str async_job_id: Id of the asynchronous job. This is the value
            of a response returned from the method that launched the job.
            :rtype: :class:`dropbox.team.TeamFolderArchiveJobStatus`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.PollError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_folder_archive_check(async_job_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_team_folder_create(
        self,
        name: str,
        sync_setting: Optional[str] = None
    ) -> DropboxResponse:
        """Creates a new, active, team folder with no members. This endpoint can

        API Endpoint: /2/team/team/folder_create
        Namespace: team
        Client type: team

        Args:
            name (str, required): Parameter for team_team_folder_create
            sync_setting (str, optional): Parameter for team_team_folder_create

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            only be used for teams that do not already have a shared team space.
            Permission : Team member file access.
            Route attributes:
            scope: team_data.content.write
            :param str name: Name for the new team folder.
            :param Nullable[:class:`dropbox.team.SyncSettingArg`] sync_setting: The
            sync setting to apply to this team folder. Only permitted if the
            team has team selective sync enabled.
            :rtype: :class:`dropbox.team.TeamFolderMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.TeamFolderCreateError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_folder_create(name, sync_setting=sync_setting))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_team_folder_get_info(
        self,
        team_folder_ids: str
    ) -> DropboxResponse:
        """Retrieves metadata for team folders. Permission : Team member file

        API Endpoint: /2/team/team/folder_get_info
        Namespace: team
        Client type: team

        Args:
            team_folder_ids (str, required): Parameter for team_team_folder_get_info

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            access.
            Route attributes:
            scope: team_data.content.read
            :param List[str] team_folder_ids: The list of team folder IDs.
            :rtype: List[:class:`dropbox.team.TeamFolderGetInfoItem`]
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_folder_get_info(team_folder_ids))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_team_folder_list(
        self,
        limit: str = 1000
    ) -> DropboxResponse:
        """Lists all team folders. Permission : Team member file access.

        API Endpoint: /2/team/team/folder_list
        Namespace: team
        Client type: team

        Args:
            limit (str, optional): Parameter for team_team_folder_list

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            Route attributes:
            scope: team_data.content.read
            :param int limit: The maximum number of results to return per request.
            :rtype: :class:`dropbox.team.TeamFolderListResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.TeamFolderListError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_team_folder_list(limit=limit))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_team_folder_list_continue(
        self,
        cursor: str
    ) -> DropboxResponse:
        """Once a cursor has been retrieved from :meth:`team_team_folder_list`, use

        API Endpoint: /2/team/team/folder_list_continue
        Namespace: team
        Client type: team

        Args:
            cursor (str, required): Parameter for team_team_folder_list_continue

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            this to paginate through all team folders. Permission : Team member file
            access.
            Route attributes:
            scope: team_data.content.read
            :param str cursor: Indicates from what point to get the next set of team
            folders.
            :rtype: :class:`dropbox.team.TeamFolderListResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.TeamFolderListContinueError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_folder_list_continue(cursor))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_team_folder_permanently_delete(
        self,
        team_folder_id: str
    ) -> DropboxResponse:
        """Permanently deletes an archived team folder. This endpoint cannot be

        API Endpoint: /2/team/team/folder_permanently_delete
        Namespace: team
        Client type: team

        Args:
            team_folder_id (str, required): Parameter for team_team_folder_permanently_delete

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            used for teams that have a shared team space. Permission : Team member
            file access.
            Route attributes:
            scope: team_data.content.write
            :param str team_folder_id: The ID of the team folder.
            :rtype: None
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_folder_permanently_delete(team_folder_id))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_team_folder_rename(
        self,
        team_folder_id: str,
        name: str
    ) -> DropboxResponse:
        """Changes an active team folder's name. Permission : Team member file

        API Endpoint: /2/team/team/folder_rename
        Namespace: team
        Client type: team

        Args:
            team_folder_id (str, required): Parameter for team_team_folder_rename
            name (str, required): Parameter for team_team_folder_rename

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            access.
            Route attributes:
            scope: team_data.content.write
            :param str name: New team folder name.
            :rtype: :class:`dropbox.team.TeamFolderMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.TeamFolderRenameError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_folder_rename(team_folder_id, name))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_team_folder_update_sync_settings(
        self,
        team_folder_id: str,
        sync_setting: Optional[str] = None,
        content_sync_settings: Optional[str] = None
    ) -> DropboxResponse:
        """Updates the sync settings on a team folder or its contents.  Use of this

        API Endpoint: /2/team/team/folder_update_sync_settings
        Namespace: team
        Client type: team

        Args:
            team_folder_id (str, required): Parameter for team_team_folder_update_sync_settings
            sync_setting (str, optional): Parameter for team_team_folder_update_sync_settings
            content_sync_settings (str, optional): Parameter for team_team_folder_update_sync_settings

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            endpoint requires that the team has team selective sync enabled.
            Route attributes:
            scope: team_data.content.write
            :param Nullable[:class:`dropbox.team.SyncSettingArg`] sync_setting: Sync
            setting to apply to the team folder itself. Only meaningful if the
            team folder is not a shared team root.
            :param Nullable[List[:class:`dropbox.team.ContentSyncSettingArg`]]
            content_sync_settings: Sync settings to apply to contents of this
            team folder.
            :rtype: :class:`dropbox.team.TeamFolderMetadata`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.TeamFolderUpdateSyncSettingsError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_folder_update_sync_settings(team_folder_id, sync_setting=sync_setting, content_sync_settings=content_sync_settings))
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    async def team_token_get_authenticated_admin(self) -> DropboxResponse:
        """Returns the member profile of the admin who generated the team access

        API Endpoint: /2/team/token/get_authenticated_admin
        Namespace: team
        Client type: team

        Returns:
            DropboxResponse: SDK response

        Original SDK Documentation:
            token used to make the call.
            Route attributes:
            scope: team_info.read
            :rtype: :class:`dropbox.team.TokenGetAuthenticatedAdminResult`
            :raises: :class:`.exceptions.ApiError`
            If this raises, ApiError will contain:
            :class:`dropbox.team.TokenGetAuthenticatedAdminError`
        """
        client = await self._get_team_client()
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.team_token_get_authenticated_admin())
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))

    def get_user_client(self) -> Dropbox:
        """Get the underlying Dropbox user client."""
        return self._get_user_client()

    def get_team_client(self) -> DropboxTeam:
        """Get the underlying Dropbox team client (if available)."""
        try:
            return self._get_team_client()
        except Exception:
            return None

    def has_team_access(self) -> bool:
        """Check if this client has team access."""
        return self.get_team_client() is not None

    def get_sdk_info(self) -> Dict[str, Union[int, bool, List[str], Dict[str, int]]]:
        """Get information about the wrapped SDK methods."""
        user_methods = [m for m in self.generated_methods if m.get('client_type') == 'user']
        team_methods = [m for m in self.generated_methods if m.get('client_type') == 'team']

        namespaces: Dict[str, int] = {}
        for method in self.generated_methods:
            namespace = method.get('namespace') or 'root'
            if namespace not in namespaces:
                namespaces[namespace] = 0
            namespaces[namespace] += 1

        endpoints = [m.get('endpoint') for m in self.generated_methods if m.get('endpoint')]

        return {
            "total_methods": len(self.generated_methods),
            "user_methods": len(user_methods),
            "team_methods": len(team_methods),
            "namespaces": namespaces,
            "has_team_access": self.has_team_access(),
            "endpoints": [e for e in endpoints if e is not None]
        }
