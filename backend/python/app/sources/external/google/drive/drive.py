from typing import Any, Dict, Optional

from app.sources.client.google.google import GoogleClient


class GoogleDriveDataSource:
    """
    Auto-generated Google Drive API client wrapper.
    Uses Google SDK client internally for all operations.
    This class wraps all Google Drive API v3 methods and provides
    a consistent interface while using the official Google SDK.
    """
    def __init__(
        self,
        client: GoogleClient
    ) -> None:
        """
        Initialize with Google Drive API client.
        Args:
            client: Google Drive API client from build('drive', 'v3', credentials=credentials)
        """
        self.client = client

    async def operations_get(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Drive API: Gets the latest state of a long-running operation. Clients can use this method to poll the operation result at intervals as recommended by the API service.

        HTTP GET operations/{name}

        Args:
            name (str, required): The name of the operation resource.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        request = self.client.operations().get(**kwargs) # type: ignore
        return request.execute()

    async def about_get(self) -> Dict[str, Any]:
        """Google Drive API: Gets information about the user, the user's Drive, and system capabilities. For more information, see [Return user info](https://developers.google.com/workspace/drive/api/guides/user-info). Required: The `fields` parameter must be set. To return the exact fields you need, see [Return specific fields](https://developers.google.com/workspace/drive/api/guides/fields-parameter).

        HTTP GET about

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        # No parameters for this method

        request = self.client.about().get(**kwargs) # type: ignore
        return request.execute()

    async def apps_get(
        self,
        appId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Gets a specific app. For more information, see [Return user info](https://developers.google.com/workspace/drive/api/guides/user-info).

        HTTP GET apps/{appId}

        Args:
            appId (str, required): The ID of the app.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if appId is not None:
            kwargs['appId'] = appId

        request = self.client.apps().get(**kwargs) # type: ignore
        return request.execute()

    async def apps_list(
        self,
        appFilterExtensions: Optional[str] = None,
        appFilterMimeTypes: Optional[str] = None,
        languageCode: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Lists a user's installed apps. For more information, see [Return user info](https://developers.google.com/workspace/drive/api/guides/user-info).

        HTTP GET apps

        Args:
            appFilterExtensions (str, optional): A comma-separated list of file extensions to limit returned results. All results within the given app query scope which can open any of the given file extensions are included in the response. If `appFilterMimeTypes` are provided as well, the result is a union of the two resulting app lists.
            appFilterMimeTypes (str, optional): A comma-separated list of file extensions to limit returned results. All results within the given app query scope which can open any of the given MIME types will be included in the response. If `appFilterExtensions` are provided as well, the result is a union of the two resulting app lists.
            languageCode (str, optional): A language or locale code, as defined by BCP 47, with some extensions from Unicode's LDML format (http://www.unicode.org/reports/tr35/).

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if appFilterExtensions is not None:
            kwargs['appFilterExtensions'] = appFilterExtensions
        if appFilterMimeTypes is not None:
            kwargs['appFilterMimeTypes'] = appFilterMimeTypes
        if languageCode is not None:
            kwargs['languageCode'] = languageCode

        request = self.client.apps().list(**kwargs) # type: ignore
        return request.execute()

    async def changes_get_start_page_token(
        self,
        driveId: Optional[str] = None,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        teamDriveId: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Gets the starting pageToken for listing future changes. For more information, see [Retrieve changes](https://developers.google.com/workspace/drive/api/guides/manage-changes).

        HTTP GET changes/startPageToken

        Args:
            driveId (str, optional): The ID of the shared drive for which the starting pageToken for listing future changes from that shared drive will be returned.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            teamDriveId (str, optional): Deprecated: Use `driveId` instead.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if driveId is not None:
            kwargs['driveId'] = driveId
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if teamDriveId is not None:
            kwargs['teamDriveId'] = teamDriveId

        request = self.client.changes().getStartPageToken(**kwargs) # type: ignore
        return request.execute()

    async def changes_list(
        self,
        pageToken: str,
        driveId: Optional[str] = None,
        includeCorpusRemovals: Optional[bool] = None,
        includeItemsFromAllDrives: Optional[bool] = None,
        includeRemoved: Optional[bool] = None,
        includeTeamDriveItems: Optional[bool] = None,
        pageSize: Optional[int] = None,
        restrictToMyDrive: Optional[bool] = None,
        spaces: Optional[str] = None,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        teamDriveId: Optional[str] = None,
        includePermissionsForView: Optional[str] = None,
        includeLabels: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Lists the changes for a user or shared drive. For more information, see [Retrieve changes](https://developers.google.com/workspace/drive/api/guides/manage-changes).

        HTTP GET changes

        Args:
            driveId (str, optional): The shared drive from which changes will be returned. If specified the change IDs will be reflective of the shared drive; use the combined drive ID and change ID as an identifier.
            includeCorpusRemovals (bool, optional): Whether changes should include the file resource if the file is still accessible by the user at the time of the request, even when a file was removed from the list of changes and there will be no further change entries for this file.
            includeItemsFromAllDrives (bool, optional): Whether both My Drive and shared drive items should be included in results.
            includeRemoved (bool, optional): Whether to include changes indicating that items have been removed from the list of changes, for example by deletion or loss of access.
            includeTeamDriveItems (bool, optional): Deprecated: Use `includeItemsFromAllDrives` instead.
            pageSize (int, optional): The maximum number of changes to return per page.
            pageToken (str, required): The token for continuing a previous list request on the next page. This should be set to the value of 'nextPageToken' from the previous response or to the response from the getStartPageToken method.
            restrictToMyDrive (bool, optional): Whether to restrict the results to changes inside the My Drive hierarchy. This omits changes to files such as those in the Application Data folder or shared files which have not been added to My Drive.
            spaces (str, optional): A comma-separated list of spaces to query within the corpora. Supported values are 'drive' and 'appDataFolder'.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            teamDriveId (str, optional): Deprecated: Use `driveId` instead.
            includePermissionsForView (str, optional): Specifies which additional view's permissions to include in the response. Only 'published' is supported.
            includeLabels (str, optional): A comma-separated list of IDs of labels to include in the `labelInfo` part of the response.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if driveId is not None:
            kwargs['driveId'] = driveId
        if includeCorpusRemovals is not None:
            kwargs['includeCorpusRemovals'] = includeCorpusRemovals
        if includeItemsFromAllDrives is not None:
            kwargs['includeItemsFromAllDrives'] = includeItemsFromAllDrives
        if includeRemoved is not None:
            kwargs['includeRemoved'] = includeRemoved
        if includeTeamDriveItems is not None:
            kwargs['includeTeamDriveItems'] = includeTeamDriveItems
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if restrictToMyDrive is not None:
            kwargs['restrictToMyDrive'] = restrictToMyDrive
        if spaces is not None:
            kwargs['spaces'] = spaces
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if teamDriveId is not None:
            kwargs['teamDriveId'] = teamDriveId
        if includePermissionsForView is not None:
            kwargs['includePermissionsForView'] = includePermissionsForView
        if includeLabels is not None:
            kwargs['includeLabels'] = includeLabels

        request = self.client.changes().list(**kwargs) # type: ignore
        return request.execute()

    async def changes_watch(
        self,
        pageToken: str,
        driveId: Optional[str] = None,
        includeCorpusRemovals: Optional[bool] = None,
        includeItemsFromAllDrives: Optional[bool] = None,
        includeRemoved: Optional[bool] = None,
        includeTeamDriveItems: Optional[bool] = None,
        pageSize: Optional[int] = None,
        restrictToMyDrive: Optional[bool] = None,
        spaces: Optional[str] = None,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        teamDriveId: Optional[str] = None,
        includePermissionsForView: Optional[str] = None,
        includeLabels: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Subscribes to changes for a user. For more information, see [Notifications for resource changes](https://developers.google.com/workspace/drive/api/guides/push).

        HTTP POST changes/watch

        Args:
            driveId (str, optional): The shared drive from which changes will be returned. If specified the change IDs will be reflective of the shared drive; use the combined drive ID and change ID as an identifier.
            includeCorpusRemovals (bool, optional): Whether changes should include the file resource if the file is still accessible by the user at the time of the request, even when a file was removed from the list of changes and there will be no further change entries for this file.
            includeItemsFromAllDrives (bool, optional): Whether both My Drive and shared drive items should be included in results.
            includeRemoved (bool, optional): Whether to include changes indicating that items have been removed from the list of changes, for example by deletion or loss of access.
            includeTeamDriveItems (bool, optional): Deprecated: Use `includeItemsFromAllDrives` instead.
            pageSize (int, optional): The maximum number of changes to return per page.
            pageToken (str, required): The token for continuing a previous list request on the next page. This should be set to the value of 'nextPageToken' from the previous response or to the response from the getStartPageToken method.
            restrictToMyDrive (bool, optional): Whether to restrict the results to changes inside the My Drive hierarchy. This omits changes to files such as those in the Application Data folder or shared files which have not been added to My Drive.
            spaces (str, optional): A comma-separated list of spaces to query within the corpora. Supported values are 'drive' and 'appDataFolder'.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            teamDriveId (str, optional): Deprecated: Use `driveId` instead.
            includePermissionsForView (str, optional): Specifies which additional view's permissions to include in the response. Only 'published' is supported.
            includeLabels (str, optional): A comma-separated list of IDs of labels to include in the `labelInfo` part of the response.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if driveId is not None:
            kwargs['driveId'] = driveId
        if includeCorpusRemovals is not None:
            kwargs['includeCorpusRemovals'] = includeCorpusRemovals
        if includeItemsFromAllDrives is not None:
            kwargs['includeItemsFromAllDrives'] = includeItemsFromAllDrives
        if includeRemoved is not None:
            kwargs['includeRemoved'] = includeRemoved
        if includeTeamDriveItems is not None:
            kwargs['includeTeamDriveItems'] = includeTeamDriveItems
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if restrictToMyDrive is not None:
            kwargs['restrictToMyDrive'] = restrictToMyDrive
        if spaces is not None:
            kwargs['spaces'] = spaces
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if teamDriveId is not None:
            kwargs['teamDriveId'] = teamDriveId
        if includePermissionsForView is not None:
            kwargs['includePermissionsForView'] = includePermissionsForView
        if includeLabels is not None:
            kwargs['includeLabels'] = includeLabels

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.changes().watch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.changes().watch(**kwargs) # type: ignore
        return request.execute()

    async def channels_stop(self) -> Dict[str, Any]:
        """Google Drive API: Stops watching resources through this channel. For more information, see [Notifications for resource changes](https://developers.google.com/workspace/drive/api/guides/push).

        HTTP POST channels/stop

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        # No parameters for this method

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.channels().stop(**kwargs, body=body) # type: ignore
        else:
            request = self.client.channels().stop(**kwargs) # type: ignore
        return request.execute()

    async def comments_create(
        self,
        fileId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Creates a comment on a file. For more information, see [Manage comments and replies](https://developers.google.com/workspace/drive/api/guides/manage-comments). Required: The `fields` parameter must be set. To return the exact fields you need, see [Return specific fields](https://developers.google.com/workspace/drive/api/guides/fields-parameter).

        HTTP POST files/{fileId}/comments

        Args:
            fileId (str, required): The ID of the file.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.comments().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.comments().create(**kwargs) # type: ignore
        return request.execute()

    async def comments_delete(
        self,
        fileId: str,
        commentId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Deletes a comment. For more information, see [Manage comments and replies](https://developers.google.com/workspace/drive/api/guides/manage-comments).

        HTTP DELETE files/{fileId}/comments/{commentId}

        Args:
            fileId (str, required): The ID of the file.
            commentId (str, required): The ID of the comment.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if commentId is not None:
            kwargs['commentId'] = commentId

        request = self.client.comments().delete(**kwargs) # type: ignore
        return request.execute()

    async def comments_get(
        self,
        fileId: str,
        commentId: str,
        includeDeleted: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Gets a comment by ID. For more information, see [Manage comments and replies](https://developers.google.com/workspace/drive/api/guides/manage-comments). Required: The `fields` parameter must be set. To return the exact fields you need, see [Return specific fields](https://developers.google.com/workspace/drive/api/guides/fields-parameter).

        HTTP GET files/{fileId}/comments/{commentId}

        Args:
            fileId (str, required): The ID of the file.
            commentId (str, required): The ID of the comment.
            includeDeleted (bool, optional): Whether to return deleted comments. Deleted comments will not include their original content.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if commentId is not None:
            kwargs['commentId'] = commentId
        if includeDeleted is not None:
            kwargs['includeDeleted'] = includeDeleted

        request = self.client.comments().get(**kwargs) # type: ignore
        return request.execute()

    async def comments_list(
        self,
        fileId: str,
        includeDeleted: Optional[bool] = None,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None,
        startModifiedTime: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Lists a file's comments. For more information, see [Manage comments and replies](https://developers.google.com/workspace/drive/api/guides/manage-comments). Required: The `fields` parameter must be set. To return the exact fields you need, see [Return specific fields](https://developers.google.com/workspace/drive/api/guides/fields-parameter).

        HTTP GET files/{fileId}/comments

        Args:
            fileId (str, required): The ID of the file.
            includeDeleted (bool, optional): Whether to include deleted comments. Deleted comments will not include their original content.
            pageSize (int, optional): The maximum number of comments to return per page.
            pageToken (str, optional): The token for continuing a previous list request on the next page. This should be set to the value of 'nextPageToken' from the previous response.
            startModifiedTime (str, optional): The minimum value of 'modifiedTime' for the result comments (RFC 3339 date-time).

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if includeDeleted is not None:
            kwargs['includeDeleted'] = includeDeleted
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if startModifiedTime is not None:
            kwargs['startModifiedTime'] = startModifiedTime

        request = self.client.comments().list(**kwargs) # type: ignore
        return request.execute()

    async def comments_update(
        self,
        fileId: str,
        commentId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Updates a comment with patch semantics. For more information, see [Manage comments and replies](https://developers.google.com/workspace/drive/api/guides/manage-comments). Required: The `fields` parameter must be set. To return the exact fields you need, see [Return specific fields](https://developers.google.com/workspace/drive/api/guides/fields-parameter).

        HTTP PATCH files/{fileId}/comments/{commentId}

        Args:
            fileId (str, required): The ID of the file.
            commentId (str, required): The ID of the comment.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if commentId is not None:
            kwargs['commentId'] = commentId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.comments().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.comments().update(**kwargs) # type: ignore
        return request.execute()

    async def drives_create(
        self,
        requestId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Creates a shared drive. For more information, see [Manage shared drives](https://developers.google.com/workspace/drive/api/guides/manage-shareddrives).

        HTTP POST drives

        Args:
            requestId (str, required): Required. An ID, such as a random UUID, which uniquely identifies this user's request for idempotent creation of a shared drive. A repeated request by the same user and with the same request ID will avoid creating duplicates by attempting to create the same shared drive. If the shared drive already exists a 409 error will be returned.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if requestId is not None:
            kwargs['requestId'] = requestId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.drives().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.drives().create(**kwargs) # type: ignore
        return request.execute()

    async def drives_delete(
        self,
        driveId: str,
        useDomainAdminAccess: Optional[bool] = None,
        allowItemDeletion: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Permanently deletes a shared drive for which the user is an `organizer`. The shared drive cannot contain any untrashed items. For more information, see [Manage shared drives](https://developers.google.com/workspace/drive/api/guides/manage-shareddrives).

        HTTP DELETE drives/{driveId}

        Args:
            driveId (str, required): The ID of the shared drive.
            useDomainAdminAccess (bool, optional): Issue the request as a domain administrator; if set to true, then the requester will be granted access if they are an administrator of the domain to which the shared drive belongs.
            allowItemDeletion (bool, optional): Whether any items inside the shared drive should also be deleted. This option is only supported when `useDomainAdminAccess` is also set to `true`.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if driveId is not None:
            kwargs['driveId'] = driveId
        if useDomainAdminAccess is not None:
            kwargs['useDomainAdminAccess'] = useDomainAdminAccess
        if allowItemDeletion is not None:
            kwargs['allowItemDeletion'] = allowItemDeletion

        request = self.client.drives().delete(**kwargs) # type: ignore
        return request.execute()

    async def drives_get(
        self,
        driveId: str,
        useDomainAdminAccess: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Gets a shared drive's metadata by ID. For more information, see [Manage shared drives](https://developers.google.com/workspace/drive/api/guides/manage-shareddrives).

        HTTP GET drives/{driveId}

        Args:
            driveId (str, required): The ID of the shared drive.
            useDomainAdminAccess (bool, optional): Issue the request as a domain administrator; if set to true, then the requester will be granted access if they are an administrator of the domain to which the shared drive belongs.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if driveId is not None:
            kwargs['driveId'] = driveId
        if useDomainAdminAccess is not None:
            kwargs['useDomainAdminAccess'] = useDomainAdminAccess

        request = self.client.drives().get(**kwargs) # type: ignore
        return request.execute()

    async def drives_hide(
        self,
        driveId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Hides a shared drive from the default view. For more information, see [Manage shared drives](https://developers.google.com/workspace/drive/api/guides/manage-shareddrives).

        HTTP POST drives/{driveId}/hide

        Args:
            driveId (str, required): The ID of the shared drive.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if driveId is not None:
            kwargs['driveId'] = driveId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.drives().hide(**kwargs, body=body) # type: ignore
        else:
            request = self.client.drives().hide(**kwargs) # type: ignore
        return request.execute()

    async def drives_list(
        self,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None,
        q: Optional[str] = None,
        useDomainAdminAccess: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API:  Lists the user's shared drives. This method accepts the `q` parameter, which is a search query combining one or more search terms. For more information, see the [Search for shared drives](/workspace/drive/api/guides/search-shareddrives) guide.

        HTTP GET drives

        Args:
            pageSize (int, optional): Maximum number of shared drives to return per page.
            pageToken (str, optional): Page token for shared drives.
            q (str, optional): Query string for searching shared drives.
            useDomainAdminAccess (bool, optional): Issue the request as a domain administrator; if set to true, then all shared drives of the domain in which the requester is an administrator are returned.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if q is not None:
            kwargs['q'] = q
        if useDomainAdminAccess is not None:
            kwargs['useDomainAdminAccess'] = useDomainAdminAccess

        request = self.client.drives().list(**kwargs) # type: ignore
        return request.execute()

    async def drives_unhide(
        self,
        driveId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Restores a shared drive to the default view. For more information, see [Manage shared drives](https://developers.google.com/workspace/drive/api/guides/manage-shareddrives).

        HTTP POST drives/{driveId}/unhide

        Args:
            driveId (str, required): The ID of the shared drive.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if driveId is not None:
            kwargs['driveId'] = driveId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.drives().unhide(**kwargs, body=body) # type: ignore
        else:
            request = self.client.drives().unhide(**kwargs) # type: ignore
        return request.execute()

    async def drives_update(
        self,
        driveId: str,
        useDomainAdminAccess: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Updates the metadata for a shared drive. For more information, see [Manage shared drives](https://developers.google.com/workspace/drive/api/guides/manage-shareddrives).

        HTTP PATCH drives/{driveId}

        Args:
            driveId (str, required): The ID of the shared drive.
            useDomainAdminAccess (bool, optional): Issue the request as a domain administrator; if set to true, then the requester will be granted access if they are an administrator of the domain to which the shared drive belongs.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if driveId is not None:
            kwargs['driveId'] = driveId
        if useDomainAdminAccess is not None:
            kwargs['useDomainAdminAccess'] = useDomainAdminAccess

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.drives().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.drives().update(**kwargs) # type: ignore
        return request.execute()

    async def files_copy(
        self,
        fileId: str,
        enforceSingleParent: Optional[bool] = None,
        ignoreDefaultVisibility: Optional[bool] = None,
        keepRevisionForever: Optional[bool] = None,
        ocrLanguage: Optional[str] = None,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        includePermissionsForView: Optional[str] = None,
        includeLabels: Optional[str] = None,
        body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Creates a copy of a file and applies any requested updates with patch semantics.

        HTTP POST files/{fileId}/copy

        Args:
            fileId (str, required): The ID of the file.
            enforceSingleParent (bool, optional): Deprecated. Copying files into multiple folders is no longer supported. Use shortcuts instead.
            ignoreDefaultVisibility (bool, optional): Whether to ignore the domain's default visibility settings for the created file. Domain administrators can choose to make all uploaded files visible to the domain by default; this parameter bypasses that behavior for the request. Permissions are still inherited from parent folders.
            keepRevisionForever (bool, optional): Whether to set the 'keepForever' field in the new head revision. This is only applicable to files with binary content in Google Drive. Only 200 revisions for the file can be kept forever. If the limit is reached, try deleting pinned revisions.
            ocrLanguage (str, optional): A language hint for OCR processing during image import (ISO 639-1 code).
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            includePermissionsForView (str, optional): Specifies which additional view's permissions to include in the response. Only 'published' is supported.
            includeLabels (str, optional): A comma-separated list of IDs of labels to include in the `labelInfo` part of the response.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if enforceSingleParent is not None:
            kwargs['enforceSingleParent'] = enforceSingleParent
        if ignoreDefaultVisibility is not None:
            kwargs['ignoreDefaultVisibility'] = ignoreDefaultVisibility
        if keepRevisionForever is not None:
            kwargs['keepRevisionForever'] = keepRevisionForever
        if ocrLanguage is not None:
            kwargs['ocrLanguage'] = ocrLanguage
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if includePermissionsForView is not None:
            kwargs['includePermissionsForView'] = includePermissionsForView
        if includeLabels is not None:
            kwargs['includeLabels'] = includeLabels

        # Handle request body if provided
        if body is not None:
            request = self.client.files().copy(**kwargs, body=body) # type: ignore
        else:
            request = self.client.files().copy(**kwargs) # type: ignore
        return request.execute()

    async def files_create(
        self,
        enforceSingleParent: Optional[bool] = None,
        ignoreDefaultVisibility: Optional[bool] = None,
        keepRevisionForever: Optional[bool] = None,
        ocrLanguage: Optional[str] = None,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        useContentAsIndexableText: Optional[bool] = None,
        includePermissionsForView: Optional[str] = None,
        includeLabels: Optional[str] = None,
        body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Google Drive API:  Creates a new file. This method supports an */upload* URI and accepts uploaded media with the following characteristics: - *Maximum file size:* 5,120 GB - *Accepted Media MIME types:*`*/*` Note: Specify a valid MIME type, rather than the literal `*/*` value. The literal `*/*` is only used to indicate that any valid MIME type can be uploaded. For more information on uploading files, see [Upload file data](/workspace/drive/api/guides/manage-uploads). Apps creating shortcuts with `files.create` must specify the MIME type `application/vnd.google-apps.shortcut`. Apps should specify a file extension in the `name` property when inserting files with the API. For example, an operation to insert a JPEG file should specify something like `"name": "cat.jpg"` in the metadata. Subsequent `GET` requests include the read-only `fileExtension` property populated with the extension originally specified in the `title` property. When a Google Drive user requests to download a file, or when the file is downloaded through the sync client, Drive builds a full filename (with extension) based on the title. In cases where the extension is missing, Drive attempts to determine the extension based on the file's MIME type.

        HTTP POST files

        Args:
            enforceSingleParent (bool, optional): Deprecated. Creating files in multiple folders is no longer supported.
            ignoreDefaultVisibility (bool, optional): Whether to ignore the domain's default visibility settings for the created file. Domain administrators can choose to make all uploaded files visible to the domain by default; this parameter bypasses that behavior for the request. Permissions are still inherited from parent folders.
            keepRevisionForever (bool, optional): Whether to set the 'keepForever' field in the new head revision. This is only applicable to files with binary content in Google Drive. Only 200 revisions for the file can be kept forever. If the limit is reached, try deleting pinned revisions.
            ocrLanguage (str, optional): A language hint for OCR processing during image import (ISO 639-1 code).
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            useContentAsIndexableText (bool, optional): Whether to use the uploaded content as indexable text.
            includePermissionsForView (str, optional): Specifies which additional view's permissions to include in the response. Only 'published' is supported.
            includeLabels (str, optional): A comma-separated list of IDs of labels to include in the `labelInfo` part of the response.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if enforceSingleParent is not None:
            kwargs['enforceSingleParent'] = enforceSingleParent
        if ignoreDefaultVisibility is not None:
            kwargs['ignoreDefaultVisibility'] = ignoreDefaultVisibility
        if keepRevisionForever is not None:
            kwargs['keepRevisionForever'] = keepRevisionForever
        if ocrLanguage is not None:
            kwargs['ocrLanguage'] = ocrLanguage
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if useContentAsIndexableText is not None:
            kwargs['useContentAsIndexableText'] = useContentAsIndexableText
        if includePermissionsForView is not None:
            kwargs['includePermissionsForView'] = includePermissionsForView
        if includeLabels is not None:
            kwargs['includeLabels'] = includeLabels

        # Handle request body if provided
        if body is not None:
            request = self.client.files().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.files().create(**kwargs) # type: ignore
        return request.execute()

    async def files_create_with_media(
        self,
        file_metadata: Dict[str, Any],
        content: bytes,
        mime_type: str,
        enforceSingleParent: Optional[bool] = None,
        ignoreDefaultVisibility: Optional[bool] = None,
        keepRevisionForever: Optional[bool] = None,
        ocrLanguage: Optional[str] = None,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        useContentAsIndexableText: Optional[bool] = None,
        includePermissionsForView: Optional[str] = None,
        includeLabels: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Creates a new file with content upload.

        Args:
            file_metadata: File metadata including name, parents, etc.
            content: File content as bytes
            mime_type: MIME type of the file
            enforceSingleParent: Deprecated. Creating files in multiple folders is no longer supported.
            ignoreDefaultVisibility: Whether to ignore the domain's default visibility settings
            keepRevisionForever: Whether to set the 'keepForever' field in the new head revision
            ocrLanguage: A language hint for OCR processing during image import
            supportsAllDrives: Whether the requesting application supports both My Drives and shared drives
            supportsTeamDrives: Deprecated: Use `supportsAllDrives` instead
            useContentAsIndexableText: Whether to use the uploaded content as indexable text
            includePermissionsForView: Specifies which additional view's permissions to include
            includeLabels: A comma-separated list of IDs of labels to include

        Returns:
            Dict[str, Any]: API response with uploaded file details
        """
        import io

        from googleapiclient.http import MediaIoBaseUpload

        kwargs = {}
        if enforceSingleParent is not None:
            kwargs['enforceSingleParent'] = enforceSingleParent
        if ignoreDefaultVisibility is not None:
            kwargs['ignoreDefaultVisibility'] = ignoreDefaultVisibility
        if keepRevisionForever is not None:
            kwargs['keepRevisionForever'] = keepRevisionForever
        if ocrLanguage is not None:
            kwargs['ocrLanguage'] = ocrLanguage
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if useContentAsIndexableText is not None:
            kwargs['useContentAsIndexableText'] = useContentAsIndexableText
        if includePermissionsForView is not None:
            kwargs['includePermissionsForView'] = includePermissionsForView
        if includeLabels is not None:
            kwargs['includeLabels'] = includeLabels

        # Create a file-like object from the content
        content_file = io.BytesIO(content)
        media = MediaIoBaseUpload(content_file, mimetype=mime_type, resumable=True)

        # Create the file with media upload
        request = self.client.files().create(
            body=file_metadata,
            media_body=media,
            fields='id,name,mimeType,webViewLink,parents,size',
            **kwargs
        ) # type: ignore
        return request.execute()

    async def files_delete(
        self,
        fileId: str,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        enforceSingleParent: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Permanently deletes a file owned by the user without moving it to the trash. If the file belongs to a shared drive, the user must be an `organizer` on the parent folder. If the target is a folder, all descendants owned by the user are also deleted.

        HTTP DELETE files/{fileId}

        Args:
            fileId (str, required): The ID of the file.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            enforceSingleParent (bool, optional): Deprecated: If an item is not in a shared drive and its last parent is deleted but the item itself is not, the item will be placed under its owner's root.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if enforceSingleParent is not None:
            kwargs['enforceSingleParent'] = enforceSingleParent

        request = self.client.files().delete(**kwargs) # type: ignore
        return request.execute()

    async def files_empty_trash(
        self,
        enforceSingleParent: Optional[bool] = None,
        driveId: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Permanently deletes all of the user's trashed files.

        HTTP DELETE files/trash

        Args:
            enforceSingleParent (bool, optional): Deprecated: If an item is not in a shared drive and its last parent is deleted but the item itself is not, the item will be placed under its owner's root.
            driveId (str, optional): If set, empties the trash of the provided shared drive.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if enforceSingleParent is not None:
            kwargs['enforceSingleParent'] = enforceSingleParent
        if driveId is not None:
            kwargs['driveId'] = driveId

        request = self.client.files().emptyTrash(**kwargs) # type: ignore
        return request.execute()

    async def files_export(
        self,
        fileId: str,
        mimeType: str
    ) -> Dict[str, Any]:
        """Google Drive API: Exports a Google Workspace document to the requested MIME type and returns exported byte content. Note that the exported content is limited to 10MB.

        HTTP GET files/{fileId}/export

        Args:
            fileId (str, required): The ID of the file.
            mimeType (str, required): Required. The MIME type of the format requested for this export.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if mimeType is not None:
            kwargs['mimeType'] = mimeType

        request = self.client.files().export(**kwargs) # type: ignore
        return request.execute()

    async def files_generate_ids(
        self,
        count: Optional[int] = None,
        space: Optional[str] = None,
        type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Generates a set of file IDs which can be provided in create or copy requests.

        HTTP GET files/generateIds

        Args:
            count (int, optional): The number of IDs to return.
            space (str, optional): The space in which the IDs can be used to create new files. Supported values are 'drive' and 'appDataFolder'. (Default: 'drive')
            type (str, optional): The type of items which the IDs can be used for. Supported values are 'files' and 'shortcuts'. Note that 'shortcuts' are only supported in the `drive` 'space'. (Default: 'files')

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if count is not None:
            kwargs['count'] = count
        if space is not None:
            kwargs['space'] = space
        if type is not None:
            kwargs['type'] = type

        request = self.client.files().generateIds(**kwargs) # type: ignore
        return request.execute()

    async def files_get(
        self,
        fileId: str,
        acknowledgeAbuse: Optional[bool] = None,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        includePermissionsForView: Optional[str] = None,
        includeLabels: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API:  Gets a file's metadata or content by ID. If you provide the URL parameter `alt=media`, then the response includes the file contents in the response body. Downloading content with `alt=media` only works if the file is stored in Drive. To download Google Docs, Sheets, and Slides use [`files.export`](/workspace/drive/api/reference/rest/v3/files/export) instead. For more information, see [Download & export files](/workspace/drive/api/guides/manage-downloads).

        HTTP GET files/{fileId}

        Args:
            fileId (str, required): The ID of the file.
            acknowledgeAbuse (bool, optional): Whether the user is acknowledging the risk of downloading known malware or other abusive files. This is only applicable when the `alt` parameter is set to `media` and the user is the owner of the file or an organizer of the shared drive in which the file resides.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            includePermissionsForView (str, optional): Specifies which additional view's permissions to include in the response. Only 'published' is supported.
            includeLabels (str, optional): A comma-separated list of IDs of labels to include in the `labelInfo` part of the response.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if acknowledgeAbuse is not None:
            kwargs['acknowledgeAbuse'] = acknowledgeAbuse
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if includePermissionsForView is not None:
            kwargs['includePermissionsForView'] = includePermissionsForView
        if includeLabels is not None:
            kwargs['includeLabels'] = includeLabels

        request = self.client.files().get(**kwargs) # type: ignore
        return request.execute()

    async def files_list(
        self,
        corpora: Optional[str] = None,
        corpus: Optional[str] = None,
        driveId: Optional[str] = None,
        includeItemsFromAllDrives: Optional[bool] = None,
        includeTeamDriveItems: Optional[bool] = None,
        orderBy: Optional[str] = None,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None,
        q: Optional[str] = None,
        spaces: Optional[str] = None,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        teamDriveId: Optional[str] = None,
        includePermissionsForView: Optional[str] = None,
        includeLabels: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API:  Lists the user's files. This method accepts the `q` parameter, which is a search query combining one or more search terms. For more information, see the [Search for files & folders](/workspace/drive/api/guides/search-files) guide. *Note:* This method returns *all* files by default, including trashed files. If you don't want trashed files to appear in the list, use the `trashed=false` query parameter to remove trashed files from the results.

        HTTP GET files

        Args:
            corpora (str, optional): Bodies of items (files/documents) to which the query applies. Supported bodies are 'user', 'domain', 'drive', and 'allDrives'. Prefer 'user' or 'drive' to 'allDrives' for efficiency. By default, corpora is set to 'user'. However, this can change depending on the filter set through the 'q' parameter.
            corpus (str, optional): Deprecated: The source of files to list. Use 'corpora' instead.
            driveId (str, optional): ID of the shared drive to search.
            includeItemsFromAllDrives (bool, optional): Whether both My Drive and shared drive items should be included in results.
            includeTeamDriveItems (bool, optional): Deprecated: Use `includeItemsFromAllDrives` instead.
            orderBy (str, optional): A comma-separated list of sort keys. Valid keys are: * `createdTime`: When the file was created. * `folder`: The folder ID. This field is sorted using alphabetical ordering. * `modifiedByMeTime`: The last time the file was modified by the user. * `modifiedTime`: The last time the file was modified by anyone. * `name`: The name of the file. This field is sorted using alphabetical ordering, so 1, 12, 2, 22. * `name_natural`: The name of the file. This field is sorted using natural sort ordering, so 1, 2, 12, 22. * `quotaBytesUsed`: The number of storage quota bytes used by the file. * `recency`: The most recent timestamp from the file's date-time fields. * `sharedWithMeTime`: When the file was shared with the user, if applicable. * `starred`: Whether the user has starred the file. * `viewedByMeTime`: The last time the file was viewed by the user. Each key sorts ascending by default, but can be reversed with the 'desc' modifier. Example usage: `?orderBy=folder,modifiedTime desc,name`.
            pageSize (int, optional): The maximum number of files to return per page. Partial or empty result pages are possible even before the end of the files list has been reached.
            pageToken (str, optional): The token for continuing a previous list request on the next page. This should be set to the value of 'nextPageToken' from the previous response.
            q (str, optional): A query for filtering the file results. See the "Search for files & folders" guide for supported syntax.
            spaces (str, optional): A comma-separated list of spaces to query within the corpora. Supported values are 'drive' and 'appDataFolder'.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            teamDriveId (str, optional): Deprecated: Use `driveId` instead.
            includePermissionsForView (str, optional): Specifies which additional view's permissions to include in the response. Only 'published' is supported.
            includeLabels (str, optional): A comma-separated list of IDs of labels to include in the `labelInfo` part of the response.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if corpora is not None:
            kwargs['corpora'] = corpora
        if corpus is not None:
            kwargs['corpus'] = corpus
        if driveId is not None:
            kwargs['driveId'] = driveId
        if includeItemsFromAllDrives is not None:
            kwargs['includeItemsFromAllDrives'] = includeItemsFromAllDrives
        if includeTeamDriveItems is not None:
            kwargs['includeTeamDriveItems'] = includeTeamDriveItems
        if orderBy is not None:
            kwargs['orderBy'] = orderBy
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if q is not None:
            kwargs['q'] = q
        if spaces is not None:
            kwargs['spaces'] = spaces
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if teamDriveId is not None:
            kwargs['teamDriveId'] = teamDriveId
        if includePermissionsForView is not None:
            kwargs['includePermissionsForView'] = includePermissionsForView
        if includeLabels is not None:
            kwargs['includeLabels'] = includeLabels

        request = self.client.files().list(**kwargs) # type: ignore
        return request.execute()

    async def files_list_labels(
        self,
        fileId: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Lists the labels on a file.

        HTTP GET files/{fileId}/listLabels

        Args:
            fileId (str, required): The ID for the file.
            maxResults (int, optional): The maximum number of labels to return per page. When not set, defaults to 100.
            pageToken (str, optional): The token for continuing a previous list request on the next page. This should be set to the value of 'nextPageToken' from the previous response.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken

        request = self.client.files().listLabels(**kwargs) # type: ignore
        return request.execute()

    async def files_modify_labels(
        self,
        fileId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Modifies the set of labels applied to a file. Returns a list of the labels that were added or modified.

        HTTP POST files/{fileId}/modifyLabels

        Args:
            fileId (str, required): The ID of the file to which the labels belong.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.files().modifyLabels(**kwargs, body=body) # type: ignore
        else:
            request = self.client.files().modifyLabels(**kwargs) # type: ignore
        return request.execute()

    async def files_update(
        self,
        fileId: str,
        addParents: Optional[str] = None,
        enforceSingleParent: Optional[bool] = None,
        keepRevisionForever: Optional[bool] = None,
        ocrLanguage: Optional[str] = None,
        removeParents: Optional[str] = None,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        useContentAsIndexableText: Optional[bool] = None,
        includePermissionsForView: Optional[str] = None,
        includeLabels: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API:  Updates a file's metadata and/or content. When calling this method, only populate fields in the request that you want to modify. When updating fields, some fields might be changed automatically, such as `modifiedDate`. This method supports patch semantics. This method supports an */upload* URI and accepts uploaded media with the following characteristics: - *Maximum file size:* 5,120 GB - *Accepted Media MIME types:*`*/*` Note: Specify a valid MIME type, rather than the literal `*/*` value. The literal `*/*` is only used to indicate that any valid MIME type can be uploaded. For more information on uploading files, see [Upload file data](/workspace/drive/api/guides/manage-uploads).

        HTTP PATCH files/{fileId}

        Args:
            fileId (str, required): The ID of the file.
            addParents (str, optional): A comma-separated list of parent IDs to add.
            enforceSingleParent (bool, optional): Deprecated: Adding files to multiple folders is no longer supported. Use shortcuts instead.
            keepRevisionForever (bool, optional): Whether to set the 'keepForever' field in the new head revision. This is only applicable to files with binary content in Google Drive. Only 200 revisions for the file can be kept forever. If the limit is reached, try deleting pinned revisions.
            ocrLanguage (str, optional): A language hint for OCR processing during image import (ISO 639-1 code).
            removeParents (str, optional): A comma-separated list of parent IDs to remove.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            useContentAsIndexableText (bool, optional): Whether to use the uploaded content as indexable text.
            includePermissionsForView (str, optional): Specifies which additional view's permissions to include in the response. Only 'published' is supported.
            includeLabels (str, optional): A comma-separated list of IDs of labels to include in the `labelInfo` part of the response.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if addParents is not None:
            kwargs['addParents'] = addParents
        if enforceSingleParent is not None:
            kwargs['enforceSingleParent'] = enforceSingleParent
        if keepRevisionForever is not None:
            kwargs['keepRevisionForever'] = keepRevisionForever
        if ocrLanguage is not None:
            kwargs['ocrLanguage'] = ocrLanguage
        if removeParents is not None:
            kwargs['removeParents'] = removeParents
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if useContentAsIndexableText is not None:
            kwargs['useContentAsIndexableText'] = useContentAsIndexableText
        if includePermissionsForView is not None:
            kwargs['includePermissionsForView'] = includePermissionsForView
        if includeLabels is not None:
            kwargs['includeLabels'] = includeLabels

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.files().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.files().update(**kwargs) # type: ignore
        return request.execute()

    async def files_watch(
        self,
        fileId: str,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        acknowledgeAbuse: Optional[bool] = None,
        includePermissionsForView: Optional[str] = None,
        includeLabels: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Subscribes to changes to a file.

        HTTP POST files/{fileId}/watch

        Args:
            fileId (str, required): The ID of the file.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            acknowledgeAbuse (bool, optional): Whether the user is acknowledging the risk of downloading known malware or other abusive files. This is only applicable when the `alt` parameter is set to `media` and the user is the owner of the file or an organizer of the shared drive in which the file resides.
            includePermissionsForView (str, optional): Specifies which additional view's permissions to include in the response. Only 'published' is supported.
            includeLabels (str, optional): A comma-separated list of IDs of labels to include in the `labelInfo` part of the response.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if acknowledgeAbuse is not None:
            kwargs['acknowledgeAbuse'] = acknowledgeAbuse
        if includePermissionsForView is not None:
            kwargs['includePermissionsForView'] = includePermissionsForView
        if includeLabels is not None:
            kwargs['includeLabels'] = includeLabels

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.files().watch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.files().watch(**kwargs) # type: ignore
        return request.execute()

    async def files_download(
        self,
        fileId: str,
        mimeType: Optional[str] = None,
        revisionId: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Downloads content of a file. Operations are valid for 24 hours from the time of creation.

        HTTP POST files/{fileId}/download

        Args:
            fileId (str, required): Required. The ID of the file to download.
            mimeType (str, optional): Optional. The MIME type the file should be downloaded as. This field can only be set when downloading Google Workspace documents. See [Export MIME types for Google Workspace documents](/drive/api/guides/ref-export-formats) for the list of supported MIME types. If not set, a Google Workspace document is downloaded with a default MIME type. The default MIME type might change in the future.
            revisionId (str, optional): Optional. The revision ID of the file to download. This field can only be set when downloading blob files, Google Docs, and Google Sheets. Returns `INVALID_ARGUMENT` if downloading a specific revision on the file is unsupported.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if mimeType is not None:
            kwargs['mimeType'] = mimeType
        if revisionId is not None:
            kwargs['revisionId'] = revisionId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.files().download(**kwargs, body=body) # type: ignore
        else:
            request = self.client.files().download(**kwargs) # type: ignore
        return request.execute()

    async def permissions_create(
        self,
        fileId: str,
        emailMessage: Optional[str] = None,
        enforceSingleParent: Optional[bool] = None,
        moveToNewOwnersRoot: Optional[bool] = None,
        sendNotificationEmail: Optional[bool] = None,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        transferOwnership: Optional[bool] = None,
        useDomainAdminAccess: Optional[bool] = None,
        enforceExpansiveAccess: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Creates a permission for a file or shared drive. **Warning:** Concurrent permissions operations on the same file are not supported; only the last update is applied.

        HTTP POST files/{fileId}/permissions

        Args:
            fileId (str, required): The ID of the file or shared drive.
            emailMessage (str, optional): A plain text custom message to include in the notification email.
            enforceSingleParent (bool, optional): Deprecated: See `moveToNewOwnersRoot` for details.
            moveToNewOwnersRoot (bool, optional): This parameter will only take effect if the item is not in a shared drive and the request is attempting to transfer the ownership of the item. If set to `true`, the item will be moved to the new owner's My Drive root folder and all prior parents removed. If set to `false`, parents are not changed.
            sendNotificationEmail (bool, optional): Whether to send a notification email when sharing to users or groups. This defaults to true for users and groups, and is not allowed for other requests. It must not be disabled for ownership transfers.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            transferOwnership (bool, optional): Whether to transfer ownership to the specified user and downgrade the current owner to a writer. This parameter is required as an acknowledgement of the side effect.
            useDomainAdminAccess (bool, optional): Issue the request as a domain administrator; if set to true, then the requester will be granted access if the file ID parameter refers to a shared drive and the requester is an administrator of the domain to which the shared drive belongs.
            enforceExpansiveAccess (bool, optional): Whether the request should enforce expansive access rules.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if emailMessage is not None:
            kwargs['emailMessage'] = emailMessage
        if enforceSingleParent is not None:
            kwargs['enforceSingleParent'] = enforceSingleParent
        if moveToNewOwnersRoot is not None:
            kwargs['moveToNewOwnersRoot'] = moveToNewOwnersRoot
        if sendNotificationEmail is not None:
            kwargs['sendNotificationEmail'] = sendNotificationEmail
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if transferOwnership is not None:
            kwargs['transferOwnership'] = transferOwnership
        if useDomainAdminAccess is not None:
            kwargs['useDomainAdminAccess'] = useDomainAdminAccess
        if enforceExpansiveAccess is not None:
            kwargs['enforceExpansiveAccess'] = enforceExpansiveAccess

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.permissions().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.permissions().create(**kwargs) # type: ignore
        return request.execute()

    async def permissions_delete(
        self,
        fileId: str,
        permissionId: str,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        useDomainAdminAccess: Optional[bool] = None,
        enforceExpansiveAccess: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Deletes a permission. **Warning:** Concurrent permissions operations on the same file are not supported; only the last update is applied.

        HTTP DELETE files/{fileId}/permissions/{permissionId}

        Args:
            fileId (str, required): The ID of the file or shared drive.
            permissionId (str, required): The ID of the permission.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            useDomainAdminAccess (bool, optional): Issue the request as a domain administrator; if set to true, then the requester will be granted access if the file ID parameter refers to a shared drive and the requester is an administrator of the domain to which the shared drive belongs.
            enforceExpansiveAccess (bool, optional): Whether the request should enforce expansive access rules.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if permissionId is not None:
            kwargs['permissionId'] = permissionId
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if useDomainAdminAccess is not None:
            kwargs['useDomainAdminAccess'] = useDomainAdminAccess
        if enforceExpansiveAccess is not None:
            kwargs['enforceExpansiveAccess'] = enforceExpansiveAccess

        request = self.client.permissions().delete(**kwargs) # type: ignore
        return request.execute()

    async def permissions_get(
        self,
        fileId: str,
        permissionId: str,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        useDomainAdminAccess: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Gets a permission by ID.

        HTTP GET files/{fileId}/permissions/{permissionId}

        Args:
            fileId (str, required): The ID of the file.
            permissionId (str, required): The ID of the permission.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            useDomainAdminAccess (bool, optional): Issue the request as a domain administrator; if set to true, then the requester will be granted access if the file ID parameter refers to a shared drive and the requester is an administrator of the domain to which the shared drive belongs.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if permissionId is not None:
            kwargs['permissionId'] = permissionId
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if useDomainAdminAccess is not None:
            kwargs['useDomainAdminAccess'] = useDomainAdminAccess

        request = self.client.permissions().get(**kwargs) # type: ignore
        return request.execute()

    async def permissions_list(
        self,
        fileId: str,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        useDomainAdminAccess: Optional[bool] = None,
        includePermissionsForView: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Lists a file's or shared drive's permissions.

        HTTP GET files/{fileId}/permissions

        Args:
            fileId (str, required): The ID of the file or shared drive.
            pageSize (int, optional): The maximum number of permissions to return per page. When not set for files in a shared drive, at most 100 results will be returned. When not set for files that are not in a shared drive, the entire list will be returned.
            pageToken (str, optional): The token for continuing a previous list request on the next page. This should be set to the value of 'nextPageToken' from the previous response.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            useDomainAdminAccess (bool, optional): Issue the request as a domain administrator; if set to true, then the requester will be granted access if the file ID parameter refers to a shared drive and the requester is an administrator of the domain to which the shared drive belongs.
            includePermissionsForView (str, optional): Specifies which additional view's permissions to include in the response. Only 'published' is supported.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if useDomainAdminAccess is not None:
            kwargs['useDomainAdminAccess'] = useDomainAdminAccess
        if includePermissionsForView is not None:
            kwargs['includePermissionsForView'] = includePermissionsForView

        request = self.client.permissions().list(**kwargs) # type: ignore
        return request.execute()

    async def permissions_update(
        self,
        fileId: str,
        permissionId: str,
        removeExpiration: Optional[bool] = None,
        supportsAllDrives: Optional[bool] = None,
        supportsTeamDrives: Optional[bool] = None,
        transferOwnership: Optional[bool] = None,
        useDomainAdminAccess: Optional[bool] = None,
        enforceExpansiveAccess: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Updates a permission with patch semantics. **Warning:** Concurrent permissions operations on the same file are not supported; only the last update is applied.

        HTTP PATCH files/{fileId}/permissions/{permissionId}

        Args:
            fileId (str, required): The ID of the file or shared drive.
            permissionId (str, required): The ID of the permission.
            removeExpiration (bool, optional): Whether to remove the expiration date.
            supportsAllDrives (bool, optional): Whether the requesting application supports both My Drives and shared drives.
            supportsTeamDrives (bool, optional): Deprecated: Use `supportsAllDrives` instead.
            transferOwnership (bool, optional): Whether to transfer ownership to the specified user and downgrade the current owner to a writer. This parameter is required as an acknowledgement of the side effect.
            useDomainAdminAccess (bool, optional): Issue the request as a domain administrator; if set to true, then the requester will be granted access if the file ID parameter refers to a shared drive and the requester is an administrator of the domain to which the shared drive belongs.
            enforceExpansiveAccess (bool, optional): Whether the request should enforce expansive access rules.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if permissionId is not None:
            kwargs['permissionId'] = permissionId
        if removeExpiration is not None:
            kwargs['removeExpiration'] = removeExpiration
        if supportsAllDrives is not None:
            kwargs['supportsAllDrives'] = supportsAllDrives
        if supportsTeamDrives is not None:
            kwargs['supportsTeamDrives'] = supportsTeamDrives
        if transferOwnership is not None:
            kwargs['transferOwnership'] = transferOwnership
        if useDomainAdminAccess is not None:
            kwargs['useDomainAdminAccess'] = useDomainAdminAccess
        if enforceExpansiveAccess is not None:
            kwargs['enforceExpansiveAccess'] = enforceExpansiveAccess

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.permissions().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.permissions().update(**kwargs) # type: ignore
        return request.execute()

    async def replies_create(
        self,
        fileId: str,
        commentId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Creates a reply to a comment.

        HTTP POST files/{fileId}/comments/{commentId}/replies

        Args:
            fileId (str, required): The ID of the file.
            commentId (str, required): The ID of the comment.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if commentId is not None:
            kwargs['commentId'] = commentId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.replies().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.replies().create(**kwargs) # type: ignore
        return request.execute()

    async def replies_delete(
        self,
        fileId: str,
        commentId: str,
        replyId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Deletes a reply.

        HTTP DELETE files/{fileId}/comments/{commentId}/replies/{replyId}

        Args:
            fileId (str, required): The ID of the file.
            commentId (str, required): The ID of the comment.
            replyId (str, required): The ID of the reply.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if commentId is not None:
            kwargs['commentId'] = commentId
        if replyId is not None:
            kwargs['replyId'] = replyId

        request = self.client.replies().delete(**kwargs) # type: ignore
        return request.execute()

    async def replies_get(
        self,
        fileId: str,
        commentId: str,
        replyId: str,
        includeDeleted: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Gets a reply by ID.

        HTTP GET files/{fileId}/comments/{commentId}/replies/{replyId}

        Args:
            fileId (str, required): The ID of the file.
            commentId (str, required): The ID of the comment.
            replyId (str, required): The ID of the reply.
            includeDeleted (bool, optional): Whether to return deleted replies. Deleted replies will not include their original content.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if commentId is not None:
            kwargs['commentId'] = commentId
        if replyId is not None:
            kwargs['replyId'] = replyId
        if includeDeleted is not None:
            kwargs['includeDeleted'] = includeDeleted

        request = self.client.replies().get(**kwargs) # type: ignore
        return request.execute()

    async def replies_list(
        self,
        fileId: str,
        commentId: str,
        includeDeleted: Optional[bool] = None,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Lists a comment's replies.

        HTTP GET files/{fileId}/comments/{commentId}/replies

        Args:
            fileId (str, required): The ID of the file.
            commentId (str, required): The ID of the comment.
            includeDeleted (bool, optional): Whether to include deleted replies. Deleted replies will not include their original content.
            pageSize (int, optional): The maximum number of replies to return per page.
            pageToken (str, optional): The token for continuing a previous list request on the next page. This should be set to the value of 'nextPageToken' from the previous response.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if commentId is not None:
            kwargs['commentId'] = commentId
        if includeDeleted is not None:
            kwargs['includeDeleted'] = includeDeleted
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken

        request = self.client.replies().list(**kwargs) # type: ignore
        return request.execute()

    async def replies_update(
        self,
        fileId: str,
        commentId: str,
        replyId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Updates a reply with patch semantics.

        HTTP PATCH files/{fileId}/comments/{commentId}/replies/{replyId}

        Args:
            fileId (str, required): The ID of the file.
            commentId (str, required): The ID of the comment.
            replyId (str, required): The ID of the reply.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if commentId is not None:
            kwargs['commentId'] = commentId
        if replyId is not None:
            kwargs['replyId'] = replyId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.replies().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.replies().update(**kwargs) # type: ignore
        return request.execute()

    async def revisions_delete(
        self,
        fileId: str,
        revisionId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Permanently deletes a file version. You can only delete revisions for files with binary content in Google Drive, like images or videos. Revisions for other files, like Google Docs or Sheets, and the last remaining file version can't be deleted. For more information, see [Manage file revisions](https://developers.google.com/drive/api/guides/manage-revisions).

        HTTP DELETE files/{fileId}/revisions/{revisionId}

        Args:
            fileId (str, required): The ID of the file.
            revisionId (str, required): The ID of the revision.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if revisionId is not None:
            kwargs['revisionId'] = revisionId

        request = self.client.revisions().delete(**kwargs) # type: ignore
        return request.execute()

    async def revisions_get(
        self,
        fileId: str,
        revisionId: str,
        acknowledgeAbuse: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Gets a revision's metadata or content by ID. For more information, see [Manage file revisions](https://developers.google.com/workspace/drive/api/guides/manage-revisions).

        HTTP GET files/{fileId}/revisions/{revisionId}

        Args:
            fileId (str, required): The ID of the file.
            revisionId (str, required): The ID of the revision.
            acknowledgeAbuse (bool, optional): Whether the user is acknowledging the risk of downloading known malware or other abusive files. This is only applicable when the `alt` parameter is set to `media` and the user is the owner of the file or an organizer of the shared drive in which the file resides.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if revisionId is not None:
            kwargs['revisionId'] = revisionId
        if acknowledgeAbuse is not None:
            kwargs['acknowledgeAbuse'] = acknowledgeAbuse

        request = self.client.revisions().get(**kwargs) # type: ignore
        return request.execute()

    async def revisions_list(
        self,
        fileId: str,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Lists a file's revisions. For more information, see [Manage file revisions](https://developers.google.com/workspace/drive/api/guides/manage-revisions).

        HTTP GET files/{fileId}/revisions

        Args:
            fileId (str, required): The ID of the file.
            pageSize (int, optional): The maximum number of revisions to return per page.
            pageToken (str, optional): The token for continuing a previous list request on the next page. This should be set to the value of 'nextPageToken' from the previous response.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken

        request = self.client.revisions().list(**kwargs) # type: ignore
        return request.execute()

    async def revisions_update(
        self,
        fileId: str,
        revisionId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Updates a revision with patch semantics. For more information, see [Manage file revisions](https://developers.google.com/workspace/drive/api/guides/manage-revisions).

        HTTP PATCH files/{fileId}/revisions/{revisionId}

        Args:
            fileId (str, required): The ID of the file.
            revisionId (str, required): The ID of the revision.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if revisionId is not None:
            kwargs['revisionId'] = revisionId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.revisions().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.revisions().update(**kwargs) # type: ignore
        return request.execute()

    async def teamdrives_create(
        self,
        requestId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Deprecated: Use `drives.create` instead.

        HTTP POST teamdrives

        Args:
            requestId (str, required): Required. An ID, such as a random UUID, which uniquely identifies this user's request for idempotent creation of a Team Drive. A repeated request by the same user and with the same request ID will avoid creating duplicates by attempting to create the same Team Drive. If the Team Drive already exists a 409 error will be returned.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if requestId is not None:
            kwargs['requestId'] = requestId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.teamdrives().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.teamdrives().create(**kwargs) # type: ignore
        return request.execute()

    async def teamdrives_delete(
        self,
        teamDriveId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Deprecated: Use `drives.delete` instead.

        HTTP DELETE teamdrives/{teamDriveId}

        Args:
            teamDriveId (str, required): The ID of the Team Drive

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if teamDriveId is not None:
            kwargs['teamDriveId'] = teamDriveId

        request = self.client.teamdrives().delete(**kwargs) # type: ignore
        return request.execute()

    async def teamdrives_get(
        self,
        teamDriveId: str,
        useDomainAdminAccess: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Deprecated: Use `drives.get` instead.

        HTTP GET teamdrives/{teamDriveId}

        Args:
            teamDriveId (str, required): The ID of the Team Drive
            useDomainAdminAccess (bool, optional): Issue the request as a domain administrator; if set to true, then the requester will be granted access if they are an administrator of the domain to which the Team Drive belongs.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if teamDriveId is not None:
            kwargs['teamDriveId'] = teamDriveId
        if useDomainAdminAccess is not None:
            kwargs['useDomainAdminAccess'] = useDomainAdminAccess

        request = self.client.teamdrives().get(**kwargs) # type: ignore
        return request.execute()

    async def teamdrives_list(
        self,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None,
        q: Optional[str] = None,
        useDomainAdminAccess: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Deprecated: Use `drives.list` instead.

        HTTP GET teamdrives

        Args:
            pageSize (int, optional): Maximum number of Team Drives to return.
            pageToken (str, optional): Page token for Team Drives.
            q (str, optional): Query string for searching Team Drives.
            useDomainAdminAccess (bool, optional): Issue the request as a domain administrator; if set to true, then all Team Drives of the domain in which the requester is an administrator are returned.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if q is not None:
            kwargs['q'] = q
        if useDomainAdminAccess is not None:
            kwargs['useDomainAdminAccess'] = useDomainAdminAccess

        request = self.client.teamdrives().list(**kwargs) # type: ignore
        return request.execute()

    async def teamdrives_update(
        self,
        teamDriveId: str,
        useDomainAdminAccess: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Drive API: Deprecated: Use `drives.update` instead.

        HTTP PATCH teamdrives/{teamDriveId}

        Args:
            teamDriveId (str, required): The ID of the Team Drive
            useDomainAdminAccess (bool, optional): Issue the request as a domain administrator; if set to true, then the requester will be granted access if they are an administrator of the domain to which the Team Drive belongs.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if teamDriveId is not None:
            kwargs['teamDriveId'] = teamDriveId
        if useDomainAdminAccess is not None:
            kwargs['useDomainAdminAccess'] = useDomainAdminAccess

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.teamdrives().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.teamdrives().update(**kwargs) # type: ignore
        return request.execute()

    async def accessproposals_get(
        self,
        fileId: str,
        proposalId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Retrieves an AccessProposal by ID.

        HTTP GET files/{fileId}/accessproposals/{proposalId}

        Args:
            fileId (str, required): Required. The id of the item the request is on.
            proposalId (str, required): Required. The id of the access proposal to resolve.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if proposalId is not None:
            kwargs['proposalId'] = proposalId

        request = self.client.accessproposals().get(**kwargs) # type: ignore
        return request.execute()

    async def accessproposals_resolve(
        self,
        fileId: str,
        proposalId: str
    ) -> Dict[str, Any]:
        """Google Drive API: Used to approve or deny an Access Proposal.

        HTTP POST files/{fileId}/accessproposals/{proposalId}:resolve

        Args:
            fileId (str, required): Required. The id of the item the request is on.
            proposalId (str, required): Required. The id of the access proposal to resolve.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if proposalId is not None:
            kwargs['proposalId'] = proposalId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.accessproposals().resolve(**kwargs, body=body) # type: ignore
        else:
            request = self.client.accessproposals().resolve(**kwargs) # type: ignore
        return request.execute()

    async def accessproposals_list(
        self,
        fileId: str,
        pageToken: Optional[str] = None,
        pageSize: Optional[int] = None
    ) -> Dict[str, Any]:
        """Google Drive API: List the AccessProposals on a file. Note: Only approvers are able to list AccessProposals on a file. If the user is not an approver, returns a 403.

        HTTP GET files/{fileId}/accessproposals

        Args:
            fileId (str, required): Required. The id of the item the request is on.
            pageToken (str, optional): Optional. The continuation token on the list of access requests.
            pageSize (int, optional): Optional. The number of results per page

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if fileId is not None:
            kwargs['fileId'] = fileId
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if pageSize is not None:
            kwargs['pageSize'] = pageSize

        request = self.client.accessproposals().list(**kwargs) # type: ignore
        return request.execute()

    async def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
