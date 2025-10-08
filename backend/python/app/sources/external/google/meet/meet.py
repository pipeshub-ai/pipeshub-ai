from typing import Any, Dict, Optional

from app.sources.client.google.google import GoogleClient


class GoogleMeetDataSource:
    """
    Auto-generated Google Meet API client wrapper.
    Uses Google SDK client internally for all operations.
    This class wraps all Google Meet API v2 methods and provides
    a consistent interface while using the official Google SDK.
    """
    def __init__(
        self,
        client: GoogleClient
    ) -> None:
        """
        Initialize with Google Meet API client.
        Args:
            client: Google Meet API client from build('meet', 'v2', credentials=credentials)
        """
        self.client = client

    async def spaces_create(self, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Google Meet API: Creates a space.

        HTTP POST v2/spaces

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        # Handle request body if provided
        if body is not None:
            request = self.client.spaces().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spaces().create(**kwargs) # type: ignore
        return request.execute()

    async def spaces_get(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Meet API: Gets details about a meeting space. For an example, see [Get a meeting space](https://developers.google.com/workspace/meet/api/guides/meeting-spaces#get-meeting-space).

        HTTP GET v2/{+name}

        Args:
            name (str, required): Required. Resource name of the space. Format: `spaces/{space}` or `spaces/{meetingCode}`. `{space}` is the resource identifier for the space. It's a unique, server-generated ID and is case sensitive. For example, `jQCFfuBOdN5z`. `{meetingCode}` is an alias for the space. It's a typeable, unique character string and is non-case sensitive. For example, `abc-mnop-xyz`. The maximum length is 128 characters. A `meetingCode` shouldn't be stored long term as it can become dissociated from a meeting space and can be reused for different meeting spaces in the future. Generally, a `meetingCode` expires 365 days after last use. For more information, see [Learn about meeting codes in Google Meet](https://support.google.com/meet/answer/10710509). For more information, see [How Meet identifies a meeting space](https://developers.google.com/workspace/meet/api/guides/meeting-spaces#identify-meeting-space).

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        request = self.client.spaces().get(**kwargs) # type: ignore
        return request.execute()

    async def spaces_patch(
        self,
        name: str,
        updateMask: Optional[str] = None,
        body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Google Meet API: Updates details about a meeting space. For an example, see [Update a meeting space](https://developers.google.com/workspace/meet/api/guides/meeting-spaces#update-meeting-space).

        HTTP PATCH v2/{+name}

        Args:
            name (str, required): Immutable. Resource name of the space. Format: `spaces/{space}`. `{space}` is the resource identifier for the space. It's a unique, server-generated ID and is case sensitive. For example, `jQCFfuBOdN5z`. For more information, see [How Meet identifies a meeting space](https://developers.google.com/workspace/meet/api/guides/meeting-spaces#identify-meeting-space).
            updateMask (str, optional): Optional. Field mask used to specify the fields to be updated in the space. If update_mask isn't provided(not set, set with empty paths, or only has "" as paths), it defaults to update all fields provided with values in the request. Using "*" as update_mask will update all fields, including deleting fields not set in the request.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name
        if updateMask is not None:
            kwargs['updateMask'] = updateMask

        # Handle request body if provided
        if body is not None:
            request = self.client.spaces().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spaces().patch(**kwargs) # type: ignore
        return request.execute()

    async def spaces_end_active_conference(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Meet API: Ends an active conference (if there's one). For an example, see [End active conference](https://developers.google.com/workspace/meet/api/guides/meeting-spaces#end-active-conference).

        HTTP POST v2/{+name}:endActiveConference

        Args:
            name (str, required): Required. Resource name of the space. Format: `spaces/{space}`. `{space}` is the resource identifier for the space. It's a unique, server-generated ID and is case sensitive. For example, `jQCFfuBOdN5z`. For more information, see [How Meet identifies a meeting space](https://developers.google.com/workspace/meet/api/guides/meeting-spaces#identify-meeting-space).

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.spaces().endActiveConference(**kwargs, body=body) # type: ignore
        else:
            request = self.client.spaces().endActiveConference(**kwargs) # type: ignore
        return request.execute()

    async def conference_records_get(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Meet API: Gets a conference record by conference ID.

        HTTP GET v2/{+name}

        Args:
            name (str, required): Required. Resource name of the conference.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        request = self.client.conferenceRecords().get(**kwargs) # type: ignore
        return request.execute()

    async def conference_records_list(
        self,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None,
        filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Meet API: Lists the conference records. By default, ordered by start time and in descending order.

        HTTP GET v2/conferenceRecords

        Args:
            pageSize (int, optional): Optional. Maximum number of conference records to return. The service might return fewer than this value. If unspecified, at most 25 conference records are returned. The maximum value is 100; values above 100 are coerced to 100. Maximum might change in the future.
            pageToken (str, optional): Optional. Page token returned from previous List Call.
            filter (str, optional): Optional. User specified filtering condition in [EBNF format](https://en.wikipedia.org/wiki/Extended_Backus%E2%80%93Naur_form). The following are the filterable fields: * `space.meeting_code` * `space.name` * `start_time` * `end_time` For example, consider the following filters: * `space.name = "spaces/NAME"` * `space.meeting_code = "abc-mnop-xyz"` * `start_time>="2024-01-01T00:00:00.000Z" AND start_time<="2024-01-02T00:00:00.000Z"` * `end_time IS NULL`

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if filter is not None:
            kwargs['filter'] = filter

        request = self.client.conferenceRecords().list(**kwargs) # type: ignore
        return request.execute()

    async def conference_records_participants_get(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Meet API: Gets a participant by participant ID.

        HTTP GET v2/{+name}

        Args:
            name (str, required): Required. Resource name of the participant.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        request = self.client.conferenceRecords_participants().get(**kwargs) # type: ignore
        return request.execute()

    async def conference_records_participants_list(
        self,
        parent: str,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None,
        filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Meet API: Lists the participants in a conference record. By default, ordered by join time and in descending order. This API supports `fields` as standard parameters like every other API. However, when the `fields` request parameter is omitted, this API defaults to `'participants/*, next_page_token'`.

        HTTP GET v2/{+parent}/participants

        Args:
            parent (str, required): Required. Format: `conferenceRecords/{conference_record}`
            pageSize (int, optional): Maximum number of participants to return. The service might return fewer than this value. If unspecified, at most 100 participants are returned. The maximum value is 250; values above 250 are coerced to 250. Maximum might change in the future.
            pageToken (str, optional): Page token returned from previous List Call.
            filter (str, optional): Optional. User specified filtering condition in [EBNF format](https://en.wikipedia.org/wiki/Extended_Backus%E2%80%93Naur_form). The following are the filterable fields: * `earliest_start_time` * `latest_end_time` For example, `latest_end_time IS NULL` returns active participants in the conference.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if filter is not None:
            kwargs['filter'] = filter

        request = self.client.conferenceRecords_participants().list(**kwargs) # type: ignore
        return request.execute()

    async def conference_records_participants_participant_sessions_get(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Meet API: Gets a participant session by participant session ID.

        HTTP GET v2/{+name}

        Args:
            name (str, required): Required. Resource name of the participant.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        request = self.client.conferenceRecords_participants_participantSessions().get(**kwargs) # type: ignore
        return request.execute()

    async def conference_records_participants_participant_sessions_list(
        self,
        parent: str,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None,
        filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Meet API: Lists the participant sessions of a participant in a conference record. By default, ordered by join time and in descending order. This API supports `fields` as standard parameters like every other API. However, when the `fields` request parameter is omitted this API defaults to `'participantsessions/*, next_page_token'`.

        HTTP GET v2/{+parent}/participantSessions

        Args:
            parent (str, required): Required. Format: `conferenceRecords/{conference_record}/participants/{participant}`
            pageSize (int, optional): Optional. Maximum number of participant sessions to return. The service might return fewer than this value. If unspecified, at most 100 participants are returned. The maximum value is 250; values above 250 are coerced to 250. Maximum might change in the future.
            pageToken (str, optional): Optional. Page token returned from previous List Call.
            filter (str, optional): Optional. User specified filtering condition in [EBNF format](https://en.wikipedia.org/wiki/Extended_Backus%E2%80%93Naur_form). The following are the filterable fields: * `start_time` * `end_time` For example, `end_time IS NULL` returns active participant sessions in the conference record.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if filter is not None:
            kwargs['filter'] = filter

        request = self.client.conferenceRecords_participants_participantSessions().list(**kwargs) # type: ignore
        return request.execute()

    async def conference_records_recordings_get(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Meet API: Gets a recording by recording ID.

        HTTP GET v2/{+name}

        Args:
            name (str, required): Required. Resource name of the recording.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        request = self.client.conferenceRecords_recordings().get(**kwargs) # type: ignore
        return request.execute()

    async def conference_records_recordings_list(
        self,
        parent: str,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Meet API: Lists the recording resources from the conference record. By default, ordered by start time and in ascending order.

        HTTP GET v2/{+parent}/recordings

        Args:
            parent (str, required): Required. Format: `conferenceRecords/{conference_record}`
            pageSize (int, optional): Maximum number of recordings to return. The service might return fewer than this value. If unspecified, at most 10 recordings are returned. The maximum value is 100; values above 100 are coerced to 100. Maximum might change in the future.
            pageToken (str, optional): Page token returned from previous List Call.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken

        request = self.client.conferenceRecords_recordings().list(**kwargs) # type: ignore
        return request.execute()

    async def conference_records_transcripts_get(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Meet API: Gets a transcript by transcript ID.

        HTTP GET v2/{+name}

        Args:
            name (str, required): Required. Resource name of the transcript.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        request = self.client.conferenceRecords_transcripts().get(**kwargs) # type: ignore
        return request.execute()

    async def conference_records_transcripts_list(
        self,
        parent: str,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Meet API: Lists the set of transcripts from the conference record. By default, ordered by start time and in ascending order.

        HTTP GET v2/{+parent}/transcripts

        Args:
            parent (str, required): Required. Format: `conferenceRecords/{conference_record}`
            pageSize (int, optional): Maximum number of transcripts to return. The service might return fewer than this value. If unspecified, at most 10 transcripts are returned. The maximum value is 100; values above 100 are coerced to 100. Maximum might change in the future.
            pageToken (str, optional): Page token returned from previous List Call.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken

        request = self.client.conferenceRecords_transcripts().list(**kwargs) # type: ignore
        return request.execute()

    async def conference_records_transcripts_entries_get(
        self,
        name: str
    ) -> Dict[str, Any]:
        """Google Meet API: Gets a `TranscriptEntry` resource by entry ID. Note: The transcript entries returned by the Google Meet API might not match the transcription found in the Google Docs transcript file. This can occur when 1) we have interleaved speakers within milliseconds, or 2) the Google Docs transcript file is modified after generation.

        HTTP GET v2/{+name}

        Args:
            name (str, required): Required. Resource name of the `TranscriptEntry`.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if name is not None:
            kwargs['name'] = name

        request = self.client.conferenceRecords_transcripts_entries().get(**kwargs) # type: ignore
        return request.execute()

    async def conference_records_transcripts_entries_list(
        self,
        parent: str,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """Google Meet API: Lists the structured transcript entries per transcript. By default, ordered by start time and in ascending order. Note: The transcript entries returned by the Google Meet API might not match the transcription found in the Google Docs transcript file. This can occur when 1) we have interleaved speakers within milliseconds, or 2) the Google Docs transcript file is modified after generation.

        HTTP GET v2/{+parent}/entries

        Args:
            parent (str, required): Required. Format: `conferenceRecords/{conference_record}/transcripts/{transcript}`
            pageSize (int, optional): Maximum number of entries to return. The service might return fewer than this value. If unspecified, at most 10 entries are returned. The maximum value is 100; values above 100 are coerced to 100. Maximum might change in the future.
            pageToken (str, optional): Page token returned from previous List Call.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken

        request = self.client.conferenceRecords_transcripts_entries().list(**kwargs) # type: ignore
        return request.execute()

    async def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
