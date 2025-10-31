from typing import Any, Dict, Optional

from app.sources.client.google.google import GoogleClient


class GoogleCalendarDataSource:
    """
    Auto-generated Google Calendar API client wrapper.
    Uses Google SDK client internally for all operations.
    This class wraps all Google Calendar API v3 methods and provides
    a consistent interface while using the official Google SDK.
    """
    def __init__(
        self,
        client: GoogleClient
    ) -> None:
        """
        Initialize with Google Calendar API client.
        Args:
            client: Google Calendar API client from build('calendar', 'v3', credentials=credentials)
        """
        self.client = client

    async def acl_delete(
        self,
        calendarId: str,
        ruleId: str
    ) -> Dict[str, Any]:
        """Google Calendar API: Deletes an access control rule.

        HTTP DELETE calendars/{calendarId}/acl/{ruleId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            ruleId (str, required): ACL rule identifier.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if ruleId is not None:
            kwargs['ruleId'] = ruleId

        request = self.client.acl().delete(**kwargs) # type: ignore
        return request.execute()

    async def acl_get(
        self,
        calendarId: str,
        ruleId: str
    ) -> Dict[str, Any]:
        """Google Calendar API: Returns an access control rule.

        HTTP GET calendars/{calendarId}/acl/{ruleId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            ruleId (str, required): ACL rule identifier.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if ruleId is not None:
            kwargs['ruleId'] = ruleId

        request = self.client.acl().get(**kwargs) # type: ignore
        return request.execute()

    async def acl_insert(
        self,
        calendarId: str,
        sendNotifications: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Creates an access control rule.

        HTTP POST calendars/{calendarId}/acl

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            sendNotifications (bool, optional): Whether to send notifications about the calendar sharing change. Optional. The default is True.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if sendNotifications is not None:
            kwargs['sendNotifications'] = sendNotifications

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.acl().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.acl().insert(**kwargs) # type: ignore
        return request.execute()

    async def acl_list(
        self,
        calendarId: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        showDeleted: Optional[bool] = None,
        syncToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Returns the rules in the access control list for the calendar.

        HTTP GET calendars/{calendarId}/acl

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            maxResults (int, optional): Maximum number of entries returned on one result page. By default the value is 100 entries. The page size can never be larger than 250 entries. Optional.
            pageToken (str, optional): Token specifying which result page to return. Optional.
            showDeleted (bool, optional): Whether to include deleted ACLs in the result. Deleted ACLs are represented by role equal to "none". Deleted ACLs will always be included if syncToken is provided. Optional. The default is False.
            syncToken (str, optional): Token obtained from the nextSyncToken field returned on the last page of results from the previous list request. It makes the result of this list request contain only entries that have changed since then. All entries deleted since the previous list request will always be in the result set and it is not allowed to set showDeleted to False. If the syncToken expires, the server will respond with a 410 GONE response code and the client should clear its storage and perform a full synchronization without any syncToken. Learn more about incremental synchronization. Optional. The default is to return all entries.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if showDeleted is not None:
            kwargs['showDeleted'] = showDeleted
        if syncToken is not None:
            kwargs['syncToken'] = syncToken

        request = self.client.acl().list(**kwargs) # type: ignore
        return request.execute()

    async def acl_patch(
        self,
        calendarId: str,
        ruleId: str,
        sendNotifications: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Updates an access control rule. This method supports patch semantics.

        HTTP PATCH calendars/{calendarId}/acl/{ruleId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            ruleId (str, required): ACL rule identifier.
            sendNotifications (bool, optional): Whether to send notifications about the calendar sharing change. Note that there are no notifications on access removal. Optional. The default is True.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if ruleId is not None:
            kwargs['ruleId'] = ruleId
        if sendNotifications is not None:
            kwargs['sendNotifications'] = sendNotifications

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.acl().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.acl().patch(**kwargs) # type: ignore
        return request.execute()

    async def acl_update(
        self,
        calendarId: str,
        ruleId: str,
        sendNotifications: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Updates an access control rule.

        HTTP PUT calendars/{calendarId}/acl/{ruleId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            ruleId (str, required): ACL rule identifier.
            sendNotifications (bool, optional): Whether to send notifications about the calendar sharing change. Note that there are no notifications on access removal. Optional. The default is True.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if ruleId is not None:
            kwargs['ruleId'] = ruleId
        if sendNotifications is not None:
            kwargs['sendNotifications'] = sendNotifications

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.acl().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.acl().update(**kwargs) # type: ignore
        return request.execute()

    async def acl_watch(
        self,
        calendarId: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        showDeleted: Optional[bool] = None,
        syncToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Watch for changes to ACL resources.

        HTTP POST calendars/{calendarId}/acl/watch

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            maxResults (int, optional): Maximum number of entries returned on one result page. By default the value is 100 entries. The page size can never be larger than 250 entries. Optional.
            pageToken (str, optional): Token specifying which result page to return. Optional.
            showDeleted (bool, optional): Whether to include deleted ACLs in the result. Deleted ACLs are represented by role equal to "none". Deleted ACLs will always be included if syncToken is provided. Optional. The default is False.
            syncToken (str, optional): Token obtained from the nextSyncToken field returned on the last page of results from the previous list request. It makes the result of this list request contain only entries that have changed since then. All entries deleted since the previous list request will always be in the result set and it is not allowed to set showDeleted to False. If the syncToken expires, the server will respond with a 410 GONE response code and the client should clear its storage and perform a full synchronization without any syncToken. Learn more about incremental synchronization. Optional. The default is to return all entries.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if showDeleted is not None:
            kwargs['showDeleted'] = showDeleted
        if syncToken is not None:
            kwargs['syncToken'] = syncToken

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.acl().watch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.acl().watch(**kwargs) # type: ignore
        return request.execute()

    async def calendar_list_delete(
        self,
        calendarId: str
    ) -> Dict[str, Any]:
        """Google Calendar API: Removes a calendar from the user's calendar list.

        HTTP DELETE users/me/calendarList/{calendarId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId

        request = self.client.calendarList().delete(**kwargs) # type: ignore
        return request.execute()

    async def calendar_list_get(
        self,
        calendarId: str
    ) -> Dict[str, Any]:
        """Google Calendar API: Returns a calendar from the user's calendar list.

        HTTP GET users/me/calendarList/{calendarId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId

        request = self.client.calendarList().get(**kwargs) # type: ignore
        return request.execute()

    async def calendar_list_insert(
        self,
        colorRgbFormat: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Inserts an existing calendar into the user's calendar list.

        HTTP POST users/me/calendarList

        Args:
            colorRgbFormat (bool, optional): Whether to use the foregroundColor and backgroundColor fields to write the calendar colors (RGB). If this feature is used, the index-based colorId field will be set to the best matching option automatically. Optional. The default is False.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if colorRgbFormat is not None:
            kwargs['colorRgbFormat'] = colorRgbFormat

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.calendarList().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.calendarList().insert(**kwargs) # type: ignore
        return request.execute()

    async def calendar_list_list(
        self,
        maxResults: Optional[int] = None,
        minAccessRole: Optional[str] = None,
        pageToken: Optional[str] = None,
        showDeleted: Optional[bool] = None,
        showHidden: Optional[bool] = None,
        syncToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Returns the calendars on the user's calendar list.

        HTTP GET users/me/calendarList

        Args:
            maxResults (int, optional): Maximum number of entries returned on one result page. By default the value is 100 entries. The page size can never be larger than 250 entries. Optional.
            minAccessRole (str, optional): The minimum access role for the user in the returned entries. Optional. The default is no restriction.
            pageToken (str, optional): Token specifying which result page to return. Optional.
            showDeleted (bool, optional): Whether to include deleted calendar list entries in the result. Optional. The default is False.
            showHidden (bool, optional): Whether to show hidden entries. Optional. The default is False.
            syncToken (str, optional): Token obtained from the nextSyncToken field returned on the last page of results from the previous list request. It makes the result of this list request contain only entries that have changed since then. If only read-only fields such as calendar properties or ACLs have changed, the entry won't be returned. All entries deleted and hidden since the previous list request will always be in the result set and it is not allowed to set showDeleted neither showHidden to False. To ensure client state consistency minAccessRole query parameter cannot be specified together with nextSyncToken. If the syncToken expires, the server will respond with a 410 GONE response code and the client should clear its storage and perform a full synchronization without any syncToken. Learn more about incremental synchronization. Optional. The default is to return all entries.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if minAccessRole is not None:
            kwargs['minAccessRole'] = minAccessRole
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if showDeleted is not None:
            kwargs['showDeleted'] = showDeleted
        if showHidden is not None:
            kwargs['showHidden'] = showHidden
        if syncToken is not None:
            kwargs['syncToken'] = syncToken

        request = self.client.calendarList().list(**kwargs) # type: ignore
        return request.execute()

    async def calendar_list_patch(
        self,
        calendarId: str,
        colorRgbFormat: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Updates an existing calendar on the user's calendar list. This method supports patch semantics.

        HTTP PATCH users/me/calendarList/{calendarId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            colorRgbFormat (bool, optional): Whether to use the foregroundColor and backgroundColor fields to write the calendar colors (RGB). If this feature is used, the index-based colorId field will be set to the best matching option automatically. Optional. The default is False.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if colorRgbFormat is not None:
            kwargs['colorRgbFormat'] = colorRgbFormat

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.calendarList().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.calendarList().patch(**kwargs) # type: ignore
        return request.execute()

    async def calendar_list_update(
        self,
        calendarId: str,
        colorRgbFormat: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Updates an existing calendar on the user's calendar list.

        HTTP PUT users/me/calendarList/{calendarId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            colorRgbFormat (bool, optional): Whether to use the foregroundColor and backgroundColor fields to write the calendar colors (RGB). If this feature is used, the index-based colorId field will be set to the best matching option automatically. Optional. The default is False.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if colorRgbFormat is not None:
            kwargs['colorRgbFormat'] = colorRgbFormat

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.calendarList().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.calendarList().update(**kwargs) # type: ignore
        return request.execute()

    async def calendar_list_watch(
        self,
        maxResults: Optional[int] = None,
        minAccessRole: Optional[str] = None,
        pageToken: Optional[str] = None,
        showDeleted: Optional[bool] = None,
        showHidden: Optional[bool] = None,
        syncToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Watch for changes to CalendarList resources.

        HTTP POST users/me/calendarList/watch

        Args:
            maxResults (int, optional): Maximum number of entries returned on one result page. By default the value is 100 entries. The page size can never be larger than 250 entries. Optional.
            minAccessRole (str, optional): The minimum access role for the user in the returned entries. Optional. The default is no restriction.
            pageToken (str, optional): Token specifying which result page to return. Optional.
            showDeleted (bool, optional): Whether to include deleted calendar list entries in the result. Optional. The default is False.
            showHidden (bool, optional): Whether to show hidden entries. Optional. The default is False.
            syncToken (str, optional): Token obtained from the nextSyncToken field returned on the last page of results from the previous list request. It makes the result of this list request contain only entries that have changed since then. If only read-only fields such as calendar properties or ACLs have changed, the entry won't be returned. All entries deleted and hidden since the previous list request will always be in the result set and it is not allowed to set showDeleted neither showHidden to False. To ensure client state consistency minAccessRole query parameter cannot be specified together with nextSyncToken. If the syncToken expires, the server will respond with a 410 GONE response code and the client should clear its storage and perform a full synchronization without any syncToken. Learn more about incremental synchronization. Optional. The default is to return all entries.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if minAccessRole is not None:
            kwargs['minAccessRole'] = minAccessRole
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if showDeleted is not None:
            kwargs['showDeleted'] = showDeleted
        if showHidden is not None:
            kwargs['showHidden'] = showHidden
        if syncToken is not None:
            kwargs['syncToken'] = syncToken

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.calendarList().watch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.calendarList().watch(**kwargs) # type: ignore
        return request.execute()

    async def calendars_clear(
        self,
        calendarId: str
    ) -> Dict[str, Any]:
        """Google Calendar API: Clears a primary calendar. This operation deletes all events associated with the primary calendar of an account.

        HTTP POST calendars/{calendarId}/clear

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.calendars().clear(**kwargs, body=body) # type: ignore
        else:
            request = self.client.calendars().clear(**kwargs) # type: ignore
        return request.execute()

    async def calendars_delete(
        self,
        calendarId: str
    ) -> Dict[str, Any]:
        """Google Calendar API: Deletes a secondary calendar. Use calendars.clear for clearing all events on primary calendars.

        HTTP DELETE calendars/{calendarId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId

        request = self.client.calendars().delete(**kwargs) # type: ignore
        return request.execute()

    async def calendars_get(
        self,
        calendarId: str
    ) -> Dict[str, Any]:
        """Google Calendar API: Returns metadata for a calendar.

        HTTP GET calendars/{calendarId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId

        request = self.client.calendars().get(**kwargs) # type: ignore
        return request.execute()

    async def calendars_insert(self) -> Dict[str, Any]:
        """Google Calendar API: Creates a secondary calendar.

        HTTP POST calendars

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        # No parameters for this method

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.calendars().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.calendars().insert(**kwargs) # type: ignore
        return request.execute()

    async def calendars_patch(
        self,
        calendarId: str
    ) -> Dict[str, Any]:
        """Google Calendar API: Updates metadata for a calendar. This method supports patch semantics.

        HTTP PATCH calendars/{calendarId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.calendars().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.calendars().patch(**kwargs) # type: ignore
        return request.execute()

    async def calendars_update(
        self,
        calendarId: str
    ) -> Dict[str, Any]:
        """Google Calendar API: Updates metadata for a calendar.

        HTTP PUT calendars/{calendarId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.calendars().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.calendars().update(**kwargs) # type: ignore
        return request.execute()

    async def channels_stop(self) -> Dict[str, Any]:
        """Google Calendar API: Stop watching resources through this channel

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

    async def colors_get(self) -> Dict[str, Any]:
        """Google Calendar API: Returns the color definitions for calendars and events.

        HTTP GET colors

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        # No parameters for this method

        request = self.client.colors().get(**kwargs) # type: ignore
        return request.execute()

    async def events_delete(
        self,
        calendarId: str,
        eventId: str,
        sendNotifications: Optional[bool] = None,
        sendUpdates: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Deletes an event.

        HTTP DELETE calendars/{calendarId}/events/{eventId}

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            eventId (str, required): Event identifier.
            sendNotifications (bool, optional): Deprecated. Please use sendUpdates instead.  Whether to send notifications about the deletion of the event. Note that some emails might still be sent even if you set the value to false. The default is false.
            sendUpdates (str, optional): Guests who should receive notifications about the deletion of the event.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if eventId is not None:
            kwargs['eventId'] = eventId
        if sendNotifications is not None:
            kwargs['sendNotifications'] = sendNotifications
        if sendUpdates is not None:
            kwargs['sendUpdates'] = sendUpdates

        request = self.client.events().delete(**kwargs) # type: ignore
        return request.execute()

    async def events_get(
        self,
        calendarId: str,
        eventId: str,
        alwaysIncludeEmail: Optional[bool] = None,
        maxAttendees: Optional[int] = None,
        timeZone: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Returns an event based on its Google Calendar ID. To retrieve an event using its iCalendar ID, call the events.list method using the iCalUID parameter.

        HTTP GET calendars/{calendarId}/events/{eventId}

        Args:
            alwaysIncludeEmail (bool, optional): Deprecated and ignored. A value will always be returned in the email field for the organizer, creator and attendees, even if no real email address is available (i.e. a generated, non-working value will be provided).
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            eventId (str, required): Event identifier.
            maxAttendees (int, optional): The maximum number of attendees to include in the response. If there are more than the specified number of attendees, only the participant is returned. Optional.
            timeZone (str, optional): Time zone used in the response. Optional. The default is the time zone of the calendar.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if alwaysIncludeEmail is not None:
            kwargs['alwaysIncludeEmail'] = alwaysIncludeEmail
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if eventId is not None:
            kwargs['eventId'] = eventId
        if maxAttendees is not None:
            kwargs['maxAttendees'] = maxAttendees
        if timeZone is not None:
            kwargs['timeZone'] = timeZone

        request = self.client.events().get(**kwargs) # type: ignore
        return request.execute()

    async def events_import(
        self,
        calendarId: str,
        conferenceDataVersion: Optional[int] = None,
        supportsAttachments: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Imports an event. This operation is used to add a private copy of an existing event to a calendar. Only events with an eventType of default may be imported.
Deprecated behavior: If a non-default event is imported, its type will be changed to default and any event-type-specific properties it may have will be dropped.

        HTTP POST calendars/{calendarId}/events/import

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            conferenceDataVersion (int, optional): Version number of conference data supported by the API client. Version 0 assumes no conference data support and ignores conference data in the event's body. Version 1 enables support for copying of ConferenceData as well as for creating new conferences using the createRequest field of conferenceData. The default is 0.
            supportsAttachments (bool, optional): Whether API client performing operation supports event attachments. Optional. The default is False.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if conferenceDataVersion is not None:
            kwargs['conferenceDataVersion'] = conferenceDataVersion
        if supportsAttachments is not None:
            kwargs['supportsAttachments'] = supportsAttachments

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = getattr(self.client.events(), 'import')(**kwargs, body=body) # type: ignore
        else:
            request = getattr(self.client.events(), 'import')(**kwargs) # type: ignore
        return request.execute()

    async def events_insert(
        self,
        calendarId: str,
        conferenceDataVersion: Optional[int] = None,
        maxAttendees: Optional[int] = None,
        sendNotifications: Optional[bool] = None,
        sendUpdates: Optional[str] = None,
        supportsAttachments: Optional[bool] = None,
        body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Creates an event.

        HTTP POST calendars/{calendarId}/events

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            conferenceDataVersion (int, optional): Version number of conference data supported by the API client. Version 0 assumes no conference data support and ignores conference data in the event's body. Version 1 enables support for copying of ConferenceData as well as for creating new conferences using the createRequest field of conferenceData. The default is 0.
            maxAttendees (int, optional): The maximum number of attendees to include in the response. If there are more than the specified number of attendees, only the participant is returned. Optional.
            sendNotifications (bool, optional): Deprecated. Please use sendUpdates instead.  Whether to send notifications about the creation of the new event. Note that some emails might still be sent even if you set the value to false. The default is false.
            sendUpdates (str, optional): Whether to send notifications about the creation of the new event. Note that some emails might still be sent. The default is false.
            supportsAttachments (bool, optional): Whether API client performing operation supports event attachments. Optional. The default is False.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if conferenceDataVersion is not None:
            kwargs['conferenceDataVersion'] = conferenceDataVersion
        if maxAttendees is not None:
            kwargs['maxAttendees'] = maxAttendees
        if sendNotifications is not None:
            kwargs['sendNotifications'] = sendNotifications
        if sendUpdates is not None:
            kwargs['sendUpdates'] = sendUpdates
        if supportsAttachments is not None:
            kwargs['supportsAttachments'] = supportsAttachments

        # Handle request body if needed
        if body is not None:
            request = self.client.events().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.events().insert(**kwargs) # type: ignore
        return request.execute()

    async def events_instances(
        self,
        calendarId: str,
        eventId: str,
        alwaysIncludeEmail: Optional[bool] = None,
        maxAttendees: Optional[int] = None,
        maxResults: Optional[int] = None,
        originalStart: Optional[str] = None,
        pageToken: Optional[str] = None,
        showDeleted: Optional[bool] = None,
        timeMax: Optional[str] = None,
        timeMin: Optional[str] = None,
        timeZone: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Returns instances of the specified recurring event.

        HTTP GET calendars/{calendarId}/events/{eventId}/instances

        Args:
            alwaysIncludeEmail (bool, optional): Deprecated and ignored. A value will always be returned in the email field for the organizer, creator and attendees, even if no real email address is available (i.e. a generated, non-working value will be provided).
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            eventId (str, required): Recurring event identifier.
            maxAttendees (int, optional): The maximum number of attendees to include in the response. If there are more than the specified number of attendees, only the participant is returned. Optional.
            maxResults (int, optional): Maximum number of events returned on one result page. By default the value is 250 events. The page size can never be larger than 2500 events. Optional.
            originalStart (str, optional): The original start time of the instance in the result. Optional.
            pageToken (str, optional): Token specifying which result page to return. Optional.
            showDeleted (bool, optional): Whether to include deleted events (with status equals "cancelled") in the result. Cancelled instances of recurring events will still be included if singleEvents is False. Optional. The default is False.
            timeMax (str, optional): Upper bound (exclusive) for an event's start time to filter by. Optional. The default is not to filter by start time. Must be an RFC3339 timestamp with mandatory time zone offset.
            timeMin (str, optional): Lower bound (inclusive) for an event's end time to filter by. Optional. The default is not to filter by end time. Must be an RFC3339 timestamp with mandatory time zone offset.
            timeZone (str, optional): Time zone used in the response. Optional. The default is the time zone of the calendar.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if alwaysIncludeEmail is not None:
            kwargs['alwaysIncludeEmail'] = alwaysIncludeEmail
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if eventId is not None:
            kwargs['eventId'] = eventId
        if maxAttendees is not None:
            kwargs['maxAttendees'] = maxAttendees
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if originalStart is not None:
            kwargs['originalStart'] = originalStart
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if showDeleted is not None:
            kwargs['showDeleted'] = showDeleted
        if timeMax is not None:
            kwargs['timeMax'] = timeMax
        if timeMin is not None:
            kwargs['timeMin'] = timeMin
        if timeZone is not None:
            kwargs['timeZone'] = timeZone

        request = self.client.events().instances(**kwargs) # type: ignore
        return request.execute()

    async def events_list(
        self,
        calendarId: str,
        alwaysIncludeEmail: Optional[bool] = None,
        eventTypes: Optional[str] = None,
        iCalUID: Optional[str] = None,
        maxAttendees: Optional[int] = None,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        pageToken: Optional[str] = None,
        privateExtendedProperty: Optional[str] = None,
        q: Optional[str] = None,
        sharedExtendedProperty: Optional[str] = None,
        showDeleted: Optional[bool] = None,
        showHiddenInvitations: Optional[bool] = None,
        singleEvents: Optional[bool] = None,
        syncToken: Optional[str] = None,
        timeMax: Optional[str] = None,
        timeMin: Optional[str] = None,
        timeZone: Optional[str] = None,
        updatedMin: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Returns events on the specified calendar.

        HTTP GET calendars/{calendarId}/events

        Args:
            alwaysIncludeEmail (bool, optional): Deprecated and ignored.
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            eventTypes (str, optional): Event types to return. Optional. This parameter can be repeated multiple times to return events of different types. If unset, returns all event types.
            iCalUID (str, optional): Specifies an event ID in the iCalendar format to be provided in the response. Optional. Use this if you want to search for an event by its iCalendar ID.
            maxAttendees (int, optional): The maximum number of attendees to include in the response. If there are more than the specified number of attendees, only the participant is returned. Optional.
            maxResults (int, optional): Maximum number of events returned on one result page. The number of events in the resulting page may be less than this value, or none at all, even if there are more events matching the query. Incomplete pages can be detected by a non-empty nextPageToken field in the response. By default the value is 250 events. The page size can never be larger than 2500 events. Optional.
            orderBy (str, optional): The order of the events returned in the result. Optional. The default is an unspecified, stable order.
            pageToken (str, optional): Token specifying which result page to return. Optional.
            privateExtendedProperty (str, optional): Extended properties constraint specified as propertyName=value. Matches only private properties. This parameter might be repeated multiple times to return events that match all given constraints.
            q (str, optional): Free text search terms to find events that match these terms in the following fields:  - summary  - description  - location  - attendee's displayName  - attendee's email  - organizer's displayName  - organizer's email  - workingLocationProperties.officeLocation.buildingId  - workingLocationProperties.officeLocation.deskId  - workingLocationProperties.officeLocation.label  - workingLocationProperties.customLocation.label  These search terms also match predefined keywords against all display title translations of working location, out-of-office, and focus-time events. For example, searching for "Office" or "Bureau" returns working location events of type officeLocation, whereas searching for "Out of office" or "Abwesend" returns out-of-office events. Optional.
            sharedExtendedProperty (str, optional): Extended properties constraint specified as propertyName=value. Matches only shared properties. This parameter might be repeated multiple times to return events that match all given constraints.
            showDeleted (bool, optional): Whether to include deleted events (with status equals "cancelled") in the result. Cancelled instances of recurring events (but not the underlying recurring event) will still be included if showDeleted and singleEvents are both False. If showDeleted and singleEvents are both True, only single instances of deleted events (but not the underlying recurring events) are returned. Optional. The default is False.
            showHiddenInvitations (bool, optional): Whether to include hidden invitations in the result. Optional. The default is False.
            singleEvents (bool, optional): Whether to expand recurring events into instances and only return single one-off events and instances of recurring events, but not the underlying recurring events themselves. Optional. The default is False.
            syncToken (str, optional): Token obtained from the nextSyncToken field returned on the last page of results from the previous list request. It makes the result of this list request contain only entries that have changed since then. All events deleted since the previous list request will always be in the result set and it is not allowed to set showDeleted to False. There are several query parameters that cannot be specified together with nextSyncToken to ensure consistency of the client state.  These are:  - iCalUID  - orderBy  - privateExtendedProperty  - q  - sharedExtendedProperty  - timeMin  - timeMax  - updatedMin All other query parameters should be the same as for the initial synchronization to avoid undefined behavior. If the syncToken expires, the server will respond with a 410 GONE response code and the client should clear its storage and perform a full synchronization without any syncToken. Learn more about incremental synchronization. Optional. The default is to return all entries.
            timeMax (str, optional): Upper bound (exclusive) for an event's start time to filter by. Optional. The default is not to filter by start time. Must be an RFC3339 timestamp with mandatory time zone offset, for example, 2011-06-03T10:00:00-07:00, 2011-06-03T10:00:00Z. Milliseconds may be provided but are ignored. If timeMin is set, timeMax must be greater than timeMin.
            timeMin (str, optional): Lower bound (exclusive) for an event's end time to filter by. Optional. The default is not to filter by end time. Must be an RFC3339 timestamp with mandatory time zone offset, for example, 2011-06-03T10:00:00-07:00, 2011-06-03T10:00:00Z. Milliseconds may be provided but are ignored. If timeMax is set, timeMin must be smaller than timeMax.
            timeZone (str, optional): Time zone used in the response. Optional. The default is the time zone of the calendar.
            updatedMin (str, optional): Lower bound for an event's last modification time (as a RFC3339 timestamp) to filter by. When specified, entries deleted since this time will always be included regardless of showDeleted. Optional. The default is not to filter by last modification time.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if alwaysIncludeEmail is not None:
            kwargs['alwaysIncludeEmail'] = alwaysIncludeEmail
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if eventTypes is not None:
            kwargs['eventTypes'] = eventTypes
        if iCalUID is not None:
            kwargs['iCalUID'] = iCalUID
        if maxAttendees is not None:
            kwargs['maxAttendees'] = maxAttendees
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if orderBy is not None:
            kwargs['orderBy'] = orderBy
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if privateExtendedProperty is not None:
            kwargs['privateExtendedProperty'] = privateExtendedProperty
        if q is not None:
            kwargs['q'] = q
        if sharedExtendedProperty is not None:
            kwargs['sharedExtendedProperty'] = sharedExtendedProperty
        if showDeleted is not None:
            kwargs['showDeleted'] = showDeleted
        if showHiddenInvitations is not None:
            kwargs['showHiddenInvitations'] = showHiddenInvitations
        if singleEvents is not None:
            kwargs['singleEvents'] = singleEvents
        if syncToken is not None:
            kwargs['syncToken'] = syncToken
        if timeMax is not None:
            kwargs['timeMax'] = timeMax
        if timeMin is not None:
            kwargs['timeMin'] = timeMin
        if timeZone is not None:
            kwargs['timeZone'] = timeZone
        if updatedMin is not None:
            kwargs['updatedMin'] = updatedMin

        request = self.client.events().list(**kwargs) # type: ignore
        return request.execute()

    async def events_move(
        self,
        calendarId: str,
        destination: str,
        eventId: str,
        sendNotifications: Optional[bool] = None,
        sendUpdates: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Moves an event to another calendar, i.e. changes an event's organizer. Note that only default events can be moved; birthday, focusTime, fromGmail, outOfOffice and workingLocation events cannot be moved.

        HTTP POST calendars/{calendarId}/events/{eventId}/move

        Args:
            calendarId (str, required): Calendar identifier of the source calendar where the event currently is on.
            destination (str, required): Calendar identifier of the target calendar where the event is to be moved to.
            eventId (str, required): Event identifier.
            sendNotifications (bool, optional): Deprecated. Please use sendUpdates instead.  Whether to send notifications about the change of the event's organizer. Note that some emails might still be sent even if you set the value to false. The default is false.
            sendUpdates (str, optional): Guests who should receive notifications about the change of the event's organizer.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if destination is not None:
            kwargs['destination'] = destination
        if eventId is not None:
            kwargs['eventId'] = eventId
        if sendNotifications is not None:
            kwargs['sendNotifications'] = sendNotifications
        if sendUpdates is not None:
            kwargs['sendUpdates'] = sendUpdates

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.events().move(**kwargs, body=body) # type: ignore
        else:
            request = self.client.events().move(**kwargs) # type: ignore
        return request.execute()

    async def events_patch(
        self,
        calendarId: str,
        eventId: str,
        alwaysIncludeEmail: Optional[bool] = None,
        conferenceDataVersion: Optional[int] = None,
        maxAttendees: Optional[int] = None,
        sendNotifications: Optional[bool] = None,
        sendUpdates: Optional[str] = None,
        supportsAttachments: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Updates an event. This method supports patch semantics.

        HTTP PATCH calendars/{calendarId}/events/{eventId}

        Args:
            alwaysIncludeEmail (bool, optional): Deprecated and ignored. A value will always be returned in the email field for the organizer, creator and attendees, even if no real email address is available (i.e. a generated, non-working value will be provided).
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            conferenceDataVersion (int, optional): Version number of conference data supported by the API client. Version 0 assumes no conference data support and ignores conference data in the event's body. Version 1 enables support for copying of ConferenceData as well as for creating new conferences using the createRequest field of conferenceData. The default is 0.
            eventId (str, required): Event identifier.
            maxAttendees (int, optional): The maximum number of attendees to include in the response. If there are more than the specified number of attendees, only the participant is returned. Optional.
            sendNotifications (bool, optional): Deprecated. Please use sendUpdates instead.  Whether to send notifications about the event update (for example, description changes, etc.). Note that some emails might still be sent even if you set the value to false. The default is false.
            sendUpdates (str, optional): Guests who should receive notifications about the event update (for example, title changes, etc.).
            supportsAttachments (bool, optional): Whether API client performing operation supports event attachments. Optional. The default is False.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if alwaysIncludeEmail is not None:
            kwargs['alwaysIncludeEmail'] = alwaysIncludeEmail
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if conferenceDataVersion is not None:
            kwargs['conferenceDataVersion'] = conferenceDataVersion
        if eventId is not None:
            kwargs['eventId'] = eventId
        if maxAttendees is not None:
            kwargs['maxAttendees'] = maxAttendees
        if sendNotifications is not None:
            kwargs['sendNotifications'] = sendNotifications
        if sendUpdates is not None:
            kwargs['sendUpdates'] = sendUpdates
        if supportsAttachments is not None:
            kwargs['supportsAttachments'] = supportsAttachments

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.events().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.events().patch(**kwargs) # type: ignore
        return request.execute()

    async def events_quick_add(
        self,
        calendarId: str,
        text: str,
        sendNotifications: Optional[bool] = None,
        sendUpdates: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Creates an event based on a simple text string.

        HTTP POST calendars/{calendarId}/events/quickAdd

        Args:
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            sendNotifications (bool, optional): Deprecated. Please use sendUpdates instead.  Whether to send notifications about the creation of the event. Note that some emails might still be sent even if you set the value to false. The default is false.
            sendUpdates (str, optional): Guests who should receive notifications about the creation of the new event.
            text (str, required): The text describing the event to be created.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if sendNotifications is not None:
            kwargs['sendNotifications'] = sendNotifications
        if sendUpdates is not None:
            kwargs['sendUpdates'] = sendUpdates
        if text is not None:
            kwargs['text'] = text

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.events().quickAdd(**kwargs, body=body) # type: ignore
        else:
            request = self.client.events().quickAdd(**kwargs) # type: ignore
        return request.execute()

    async def events_update(
        self,
        calendarId: str,
        eventId: str,
        alwaysIncludeEmail: Optional[bool] = None,
        conferenceDataVersion: Optional[int] = None,
        maxAttendees: Optional[int] = None,
        sendNotifications: Optional[bool] = None,
        sendUpdates: Optional[str] = None,
        supportsAttachments: Optional[bool] = None,
        body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Updates an event.

        HTTP PUT calendars/{calendarId}/events/{eventId}

        Args:
            alwaysIncludeEmail (bool, optional): Deprecated and ignored. A value will always be returned in the email field for the organizer, creator and attendees, even if no real email address is available (i.e. a generated, non-working value will be provided).
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            conferenceDataVersion (int, optional): Version number of conference data supported by the API client. Version 0 assumes no conference data support and ignores conference data in the event's body. Version 1 enables support for copying of ConferenceData as well as for creating new conferences using the createRequest field of conferenceData. The default is 0.
            eventId (str, required): Event identifier.
            maxAttendees (int, optional): The maximum number of attendees to include in the response. If there are more than the specified number of attendees, only the participant is returned. Optional.
            sendNotifications (bool, optional): Deprecated. Please use sendUpdates instead.  Whether to send notifications about the event update (for example, description changes, etc.). Note that some emails might still be sent even if you set the value to false. The default is false.
            sendUpdates (str, optional): Guests who should receive notifications about the event update (for example, title changes, etc.).
            supportsAttachments (bool, optional): Whether API client performing operation supports event attachments. Optional. The default is False.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if alwaysIncludeEmail is not None:
            kwargs['alwaysIncludeEmail'] = alwaysIncludeEmail
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if conferenceDataVersion is not None:
            kwargs['conferenceDataVersion'] = conferenceDataVersion
        if eventId is not None:
            kwargs['eventId'] = eventId
        if maxAttendees is not None:
            kwargs['maxAttendees'] = maxAttendees
        if sendNotifications is not None:
            kwargs['sendNotifications'] = sendNotifications
        if sendUpdates is not None:
            kwargs['sendUpdates'] = sendUpdates
        if supportsAttachments is not None:
            kwargs['supportsAttachments'] = supportsAttachments

        # Handle request body if needed
        if body is not None:
            request = self.client.events().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.events().update(**kwargs) # type: ignore
        return request.execute()

    async def events_watch(
        self,
        calendarId: str,
        alwaysIncludeEmail: Optional[bool] = None,
        eventTypes: Optional[str] = None,
        iCalUID: Optional[str] = None,
        maxAttendees: Optional[int] = None,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        pageToken: Optional[str] = None,
        privateExtendedProperty: Optional[str] = None,
        q: Optional[str] = None,
        sharedExtendedProperty: Optional[str] = None,
        showDeleted: Optional[bool] = None,
        showHiddenInvitations: Optional[bool] = None,
        singleEvents: Optional[bool] = None,
        syncToken: Optional[str] = None,
        timeMax: Optional[str] = None,
        timeMin: Optional[str] = None,
        timeZone: Optional[str] = None,
        updatedMin: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Watch for changes to Events resources.

        HTTP POST calendars/{calendarId}/events/watch

        Args:
            alwaysIncludeEmail (bool, optional): Deprecated and ignored.
            calendarId (str, required): Calendar identifier. To retrieve calendar IDs call the calendarList.list method. If you want to access the primary calendar of the currently logged in user, use the "primary" keyword.
            eventTypes (str, optional): Event types to return. Optional. This parameter can be repeated multiple times to return events of different types. If unset, returns all event types.
            iCalUID (str, optional): Specifies an event ID in the iCalendar format to be provided in the response. Optional. Use this if you want to search for an event by its iCalendar ID.
            maxAttendees (int, optional): The maximum number of attendees to include in the response. If there are more than the specified number of attendees, only the participant is returned. Optional.
            maxResults (int, optional): Maximum number of events returned on one result page. The number of events in the resulting page may be less than this value, or none at all, even if there are more events matching the query. Incomplete pages can be detected by a non-empty nextPageToken field in the response. By default the value is 250 events. The page size can never be larger than 2500 events. Optional.
            orderBy (str, optional): The order of the events returned in the result. Optional. The default is an unspecified, stable order.
            pageToken (str, optional): Token specifying which result page to return. Optional.
            privateExtendedProperty (str, optional): Extended properties constraint specified as propertyName=value. Matches only private properties. This parameter might be repeated multiple times to return events that match all given constraints.
            q (str, optional): Free text search terms to find events that match these terms in the following fields:  - summary  - description  - location  - attendee's displayName  - attendee's email  - organizer's displayName  - organizer's email  - workingLocationProperties.officeLocation.buildingId  - workingLocationProperties.officeLocation.deskId  - workingLocationProperties.officeLocation.label  - workingLocationProperties.customLocation.label  These search terms also match predefined keywords against all display title translations of working location, out-of-office, and focus-time events. For example, searching for "Office" or "Bureau" returns working location events of type officeLocation, whereas searching for "Out of office" or "Abwesend" returns out-of-office events. Optional.
            sharedExtendedProperty (str, optional): Extended properties constraint specified as propertyName=value. Matches only shared properties. This parameter might be repeated multiple times to return events that match all given constraints.
            showDeleted (bool, optional): Whether to include deleted events (with status equals "cancelled") in the result. Cancelled instances of recurring events (but not the underlying recurring event) will still be included if showDeleted and singleEvents are both False. If showDeleted and singleEvents are both True, only single instances of deleted events (but not the underlying recurring events) are returned. Optional. The default is False.
            showHiddenInvitations (bool, optional): Whether to include hidden invitations in the result. Optional. The default is False.
            singleEvents (bool, optional): Whether to expand recurring events into instances and only return single one-off events and instances of recurring events, but not the underlying recurring events themselves. Optional. The default is False.
            syncToken (str, optional): Token obtained from the nextSyncToken field returned on the last page of results from the previous list request. It makes the result of this list request contain only entries that have changed since then. All events deleted since the previous list request will always be in the result set and it is not allowed to set showDeleted to False. There are several query parameters that cannot be specified together with nextSyncToken to ensure consistency of the client state.  These are:  - iCalUID  - orderBy  - privateExtendedProperty  - q  - sharedExtendedProperty  - timeMin  - timeMax  - updatedMin All other query parameters should be the same as for the initial synchronization to avoid undefined behavior. If the syncToken expires, the server will respond with a 410 GONE response code and the client should clear its storage and perform a full synchronization without any syncToken. Learn more about incremental synchronization. Optional. The default is to return all entries.
            timeMax (str, optional): Upper bound (exclusive) for an event's start time to filter by. Optional. The default is not to filter by start time. Must be an RFC3339 timestamp with mandatory time zone offset, for example, 2011-06-03T10:00:00-07:00, 2011-06-03T10:00:00Z. Milliseconds may be provided but are ignored. If timeMin is set, timeMax must be greater than timeMin.
            timeMin (str, optional): Lower bound (exclusive) for an event's end time to filter by. Optional. The default is not to filter by end time. Must be an RFC3339 timestamp with mandatory time zone offset, for example, 2011-06-03T10:00:00-07:00, 2011-06-03T10:00:00Z. Milliseconds may be provided but are ignored. If timeMax is set, timeMin must be smaller than timeMax.
            timeZone (str, optional): Time zone used in the response. Optional. The default is the time zone of the calendar.
            updatedMin (str, optional): Lower bound for an event's last modification time (as a RFC3339 timestamp) to filter by. When specified, entries deleted since this time will always be included regardless of showDeleted. Optional. The default is not to filter by last modification time.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if alwaysIncludeEmail is not None:
            kwargs['alwaysIncludeEmail'] = alwaysIncludeEmail
        if calendarId is not None:
            kwargs['calendarId'] = calendarId
        if eventTypes is not None:
            kwargs['eventTypes'] = eventTypes
        if iCalUID is not None:
            kwargs['iCalUID'] = iCalUID
        if maxAttendees is not None:
            kwargs['maxAttendees'] = maxAttendees
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if orderBy is not None:
            kwargs['orderBy'] = orderBy
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if privateExtendedProperty is not None:
            kwargs['privateExtendedProperty'] = privateExtendedProperty
        if q is not None:
            kwargs['q'] = q
        if sharedExtendedProperty is not None:
            kwargs['sharedExtendedProperty'] = sharedExtendedProperty
        if showDeleted is not None:
            kwargs['showDeleted'] = showDeleted
        if showHiddenInvitations is not None:
            kwargs['showHiddenInvitations'] = showHiddenInvitations
        if singleEvents is not None:
            kwargs['singleEvents'] = singleEvents
        if syncToken is not None:
            kwargs['syncToken'] = syncToken
        if timeMax is not None:
            kwargs['timeMax'] = timeMax
        if timeMin is not None:
            kwargs['timeMin'] = timeMin
        if timeZone is not None:
            kwargs['timeZone'] = timeZone
        if updatedMin is not None:
            kwargs['updatedMin'] = updatedMin

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.events().watch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.events().watch(**kwargs) # type: ignore
        return request.execute()

    async def freebusy_query(self, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Google Calendar API: Returns free/busy information for a set of calendars.

        HTTP POST freeBusy

        Args:
            body (dict, optional): Request body containing timeMin, timeMax, and items

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        # No parameters for this method

        # Handle request body if needed
        if body is not None:
            request = self.client.freebusy().query(**kwargs, body=body) # type: ignore
        else:
            request = self.client.freebusy().query(**kwargs) # type: ignore
        return request.execute()

    async def settings_get(
        self,
        setting: str
    ) -> Dict[str, Any]:
        """Google Calendar API: Returns a single user setting.

        HTTP GET users/me/settings/{setting}

        Args:
            setting (str, required): The id of the user setting.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if setting is not None:
            kwargs['setting'] = setting

        request = self.client.settings().get(**kwargs) # type: ignore
        return request.execute()

    async def settings_list(
        self,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        syncToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Returns all user settings for the authenticated user.

        HTTP GET users/me/settings

        Args:
            maxResults (int, optional): Maximum number of entries returned on one result page. By default the value is 100 entries. The page size can never be larger than 250 entries. Optional.
            pageToken (str, optional): Token specifying which result page to return. Optional.
            syncToken (str, optional): Token obtained from the nextSyncToken field returned on the last page of results from the previous list request. It makes the result of this list request contain only entries that have changed since then. If the syncToken expires, the server will respond with a 410 GONE response code and the client should clear its storage and perform a full synchronization without any syncToken. Learn more about incremental synchronization. Optional. The default is to return all entries.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if syncToken is not None:
            kwargs['syncToken'] = syncToken

        request = self.client.settings().list(**kwargs) # type: ignore
        return request.execute()

    async def settings_watch(
        self,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        syncToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Calendar API: Watch for changes to Settings resources.

        HTTP POST users/me/settings/watch

        Args:
            maxResults (int, optional): Maximum number of entries returned on one result page. By default the value is 100 entries. The page size can never be larger than 250 entries. Optional.
            pageToken (str, optional): Token specifying which result page to return. Optional.
            syncToken (str, optional): Token obtained from the nextSyncToken field returned on the last page of results from the previous list request. It makes the result of this list request contain only entries that have changed since then. If the syncToken expires, the server will respond with a 410 GONE response code and the client should clear its storage and perform a full synchronization without any syncToken. Learn more about incremental synchronization. Optional. The default is to return all entries.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if syncToken is not None:
            kwargs['syncToken'] = syncToken

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.settings().watch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.settings().watch(**kwargs) # type: ignore
        return request.execute()

    async def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
