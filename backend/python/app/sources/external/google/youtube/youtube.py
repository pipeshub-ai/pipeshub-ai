from typing import Any, Dict, Optional

from app.sources.client.google.google import GoogleClient


class YouTubeDataSource:
    """
    Auto-generated YouTube Data API client wrapper.
    Uses Google SDK client internally for all operations.
    This class wraps all YouTube Data API v3 methods and provides
    a consistent interface while using the official Google SDK.
    """

    def __init__(
        self,
        client: GoogleClient
    ) -> None:
        """
        Initialize with YouTube Data API client.
        Args:
            client: YouTube Data API client from build('youtube', 'v3', credentials=credentials)
        """
        self.client = client

    async def abuse_reports_insert(
        self,
        part: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/abuseReports

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.abuseReports().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.abuseReports().insert(**kwargs) # type: ignore
        return request.execute()

    async def activities_list(
        self,
        part: str,
        channelId: Optional[str] = None,
        home: Optional[bool] = None,
        mine: Optional[bool] = None,
        publishedAfter: Optional[str] = None,
        publishedBefore: Optional[str] = None,
        regionCode: Optional[str] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.
        HTTP GET youtube/v3/activities
        Args:
            channelId (str, optional):
            home (bool, optional):
            mine (bool, optional):
            publishedAfter (str, optional):
            publishedBefore (str, optional):
            regionCode (str, optional):
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more activity resource properties that the API response will include. If the parameter identifies a property that contains child properties, the child properties will be included in the response. For example, in an activity resource, the snippet property contains other properties that identify the type of activity, a display title for the activity, and so forth. If you set *part=snippet*, the API response will also contain all of those nested properties.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if channelId is not None:
            kwargs['channelId'] = channelId
        if home is not None:
            kwargs['home'] = home
        if mine is not None:
            kwargs['mine'] = mine
        if publishedAfter is not None:
            kwargs['publishedAfter'] = publishedAfter
        if publishedBefore is not None:
            kwargs['publishedBefore'] = publishedBefore
        if regionCode is not None:
            kwargs['regionCode'] = regionCode
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part

        request = self.client.activities().list(**kwargs) # type: ignore
        return request.execute()

    async def captions_list(
        self,
        videoId: str,
        part: str,
        id: Optional[str] = None,
        onBehalfOf: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/captions

        Args:
            id (str, optional): Returns the captions with the given IDs for Stubby or Apiary.
            videoId (str, required): Returns the captions for the specified video.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more caption resource parts that the API response will include. The part names that you can include in the parameter value are id and snippet.
            onBehalfOf (str, optional): ID of the Google+ Page for the channel that the request is on behalf of.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The actual CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if videoId is not None:
            kwargs['videoId'] = videoId
        if part is not None:
            kwargs['part'] = part
        if onBehalfOf is not None:
            kwargs['onBehalfOf'] = onBehalfOf
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.captions().list(**kwargs) # type: ignore
        return request.execute()

    async def captions_insert(
        self,
        part: str,
        sync: Optional[bool] = None,
        onBehalfOf: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/captions

        Args:
            sync (bool, optional): Extra parameter to allow automatically syncing the uploaded caption/transcript with the audio.
            part (str, required): The *part* parameter specifies the caption resource parts that the API response will include. Set the parameter value to snippet.
            onBehalfOf (str, optional): ID of the Google+ Page for the channel that the request is be on behalf of
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The actual CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if sync is not None:
            kwargs['sync'] = sync
        if part is not None:
            kwargs['part'] = part
        if onBehalfOf is not None:
            kwargs['onBehalfOf'] = onBehalfOf
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.captions().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.captions().insert(**kwargs) # type: ignore
        return request.execute()

    async def captions_update(
        self,
        part: str,
        sync: Optional[bool] = None,
        onBehalfOf: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Updates an existing resource.

        HTTP PUT youtube/v3/captions

        Args:
            sync (bool, optional): Extra parameter to allow automatically syncing the uploaded caption/transcript with the audio.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more caption resource parts that the API response will include. The part names that you can include in the parameter value are id and snippet.
            onBehalfOf (str, optional): ID of the Google+ Page for the channel that the request is on behalf of.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The actual CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if sync is not None:
            kwargs['sync'] = sync
        if part is not None:
            kwargs['part'] = part
        if onBehalfOf is not None:
            kwargs['onBehalfOf'] = onBehalfOf
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.captions().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.captions().update(**kwargs) # type: ignore
        return request.execute()

    async def captions_delete(
        self,
        id: str,
        onBehalfOf: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes a resource.

        HTTP DELETE youtube/v3/captions

        Args:
            id (str, required):
            onBehalfOf (str, optional): ID of the Google+ Page for the channel that the request is be on behalf of
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The actual CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if onBehalfOf is not None:
            kwargs['onBehalfOf'] = onBehalfOf
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.captions().delete(**kwargs) # type: ignore
        return request.execute()

    async def captions_download(
        self,
        id: str,
        tlang: Optional[str] = None,
        tfmt: Optional[str] = None,
        onBehalfOf: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Downloads a caption track.

        HTTP GET youtube/v3/captions/{id}

        Args:
            id (str, required): The ID of the caption track to download, required for One Platform.
            tlang (str, optional): tlang is the language code; machine translate the captions into this language.
            tfmt (str, optional): Convert the captions into this format. Supported options are sbv, srt, and vtt.
            onBehalfOf (str, optional): ID of the Google+ Page for the channel that the request is be on behalf of
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The actual CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if tlang is not None:
            kwargs['tlang'] = tlang
        if tfmt is not None:
            kwargs['tfmt'] = tfmt
        if onBehalfOf is not None:
            kwargs['onBehalfOf'] = onBehalfOf
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.captions().download(**kwargs) # type: ignore
        return request.execute()

    async def channels_list(
        self,
        part: str,
        mine: Optional[bool] = None,
        id: Optional[str] = None,
        mySubscribers: Optional[bool] = None,
        categoryId: Optional[str] = None,
        managedByMe: Optional[bool] = None,
        forUsername: Optional[str] = None,
        forHandle: Optional[str] = None,
        hl: Optional[str] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/channels

        Args:
            mine (bool, optional): Return the ids of channels owned by the authenticated user.
            id (str, optional): Return the channels with the specified IDs.
            mySubscribers (bool, optional): Return the channels subscribed to the authenticated user
            categoryId (str, optional): Return the channels within the specified guide category ID.
            managedByMe (bool, optional): Return the channels managed by the authenticated user.
            forUsername (str, optional): Return the channel associated with a YouTube username.
            forHandle (str, optional): Return the channel associated with a YouTube handle.
            hl (str, optional): Stands for "host language". Specifies the localization language of the metadata to be filled into snippet.localized. The field is filled with the default metadata if there is no localization in the specified language. The parameter value must be a language code included in the list returned by the i18nLanguages.list method (e.g. en_US, es_MX).
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more channel resource properties that the API response will include. If the parameter identifies a property that contains child properties, the child properties will be included in the response. For example, in a channel resource, the contentDetails property contains other properties, such as the uploads properties. As such, if you set *part=contentDetails*, the API response will also contain all of those nested properties.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if mine is not None:
            kwargs['mine'] = mine
        if id is not None:
            kwargs['id'] = id
        if mySubscribers is not None:
            kwargs['mySubscribers'] = mySubscribers
        if categoryId is not None:
            kwargs['categoryId'] = categoryId
        if managedByMe is not None:
            kwargs['managedByMe'] = managedByMe
        if forUsername is not None:
            kwargs['forUsername'] = forUsername
        if forHandle is not None:
            kwargs['forHandle'] = forHandle
        if hl is not None:
            kwargs['hl'] = hl
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.channels().list(**kwargs) # type: ignore
        return request.execute()

    async def channels_update(
        self,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Updates an existing resource.

        HTTP PUT youtube/v3/channels

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include. The API currently only allows the parameter value to be set to either brandingSettings or invideoPromotion. (You cannot update both of those parts with a single request.) Note that this method overrides the existing values for all of the mutable properties that are contained in any parts that the parameter value specifies.
            onBehalfOfContentOwner (str, optional): The *onBehalfOfContentOwner* parameter indicates that the authenticated user is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The actual CMS account that the user authenticates with needs to be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.channels().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.channels().update(**kwargs) # type: ignore
        return request.execute()

    async def channel_banners_insert(
        self,
        channelId: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/channelBanners/insert

        Args:
            channelId (str, optional): Unused, channel_id is currently derived from the security context of the requestor.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The actual CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if channelId is not None:
            kwargs['channelId'] = channelId
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.channelBanners().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.channelBanners().insert(**kwargs) # type: ignore
        return request.execute()

    async def channel_sections_list(
        self,
        part: str,
        id: Optional[str] = None,
        mine: Optional[bool] = None,
        channelId: Optional[str] = None,
        hl: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/channelSections

        Args:
            id (str, optional): Return the ChannelSections with the given IDs for Stubby or Apiary.
            mine (bool, optional): Return the ChannelSections owned by the authenticated user.
            channelId (str, optional): Return the ChannelSections owned by the specified channel ID.
            hl (str, optional): Return content in specified language
            part (str, required): The *part* parameter specifies a comma-separated list of one or more channelSection resource properties that the API response will include. The part names that you can include in the parameter value are id, snippet, and contentDetails. If the parameter identifies a property that contains child properties, the child properties will be included in the response. For example, in a channelSection resource, the snippet property contains other properties, such as a display title for the channelSection. If you set *part=snippet*, the API response will also contain all of those nested properties.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if mine is not None:
            kwargs['mine'] = mine
        if channelId is not None:
            kwargs['channelId'] = channelId
        if hl is not None:
            kwargs['hl'] = hl
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.channelSections().list(**kwargs) # type: ignore
        return request.execute()

    async def channel_sections_insert(
        self,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/channelSections

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include. The part names that you can include in the parameter value are snippet and contentDetails.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.channelSections().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.channelSections().insert(**kwargs) # type: ignore
        return request.execute()

    async def channel_sections_update(
        self,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Updates an existing resource.

        HTTP PUT youtube/v3/channelSections

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include. The part names that you can include in the parameter value are snippet and contentDetails.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.channelSections().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.channelSections().update(**kwargs) # type: ignore
        return request.execute()

    async def channel_sections_delete(
        self,
        id: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes a resource.

        HTTP DELETE youtube/v3/channelSections

        Args:
            id (str, required):
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.channelSections().delete(**kwargs) # type: ignore
        return request.execute()

    async def comments_list(
        self,
        part: str,
        id: Optional[str] = None,
        parentId: Optional[str] = None,
        textFormat: Optional[str] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/comments

        Args:
            id (str, optional): Returns the comments with the given IDs for One Platform.
            parentId (str, optional): Returns replies to the specified comment. Note, currently YouTube features only one level of replies (ie replies to top level comments). However replies to replies may be supported in the future.
            textFormat (str, optional): The requested text format for the returned comments.
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more comment resource properties that the API response will include.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if parentId is not None:
            kwargs['parentId'] = parentId
        if textFormat is not None:
            kwargs['textFormat'] = textFormat
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part

        request = self.client.comments().list(**kwargs) # type: ignore
        return request.execute()

    async def comments_insert(
        self,
        part: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/comments

        Args:
            part (str, required): The *part* parameter identifies the properties that the API response will include. Set the parameter value to snippet. The snippet part has a quota cost of 2 units.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.comments().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.comments().insert(**kwargs) # type: ignore
        return request.execute()

    async def comments_update(
        self,
        part: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Updates an existing resource.

        HTTP PUT youtube/v3/comments

        Args:
            part (str, required): The *part* parameter identifies the properties that the API response will include. You must at least include the snippet part in the parameter value since that part contains all of the properties that the API request can update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.comments().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.comments().update(**kwargs) # type: ignore
        return request.execute()

    async def comments_delete(
        self,
        id: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes a resource.

        HTTP DELETE youtube/v3/comments

        Args:
            id (str, required):
        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id

        request = self.client.comments().delete(**kwargs) # type: ignore
        return request.execute()

    async def comments_set_moderation_status(
        self,
        id: str,
        moderationStatus: str,
        banAuthor: Optional[bool] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Sets the moderation status of one or more comments.

        HTTP POST youtube/v3/comments/setModerationStatus

        Args:
            id (str, required): Modifies the moderation status of the comments with the given IDs
            moderationStatus (str, required): Specifies the requested moderation status. Note, comments can be in statuses, which are not available through this call. For example, this call does not allow to mark a comment as 'likely spam'. Valid values: 'heldForReview', 'published' or 'rejected'.
            banAuthor (bool, optional): If set to true the author of the comment gets added to the ban list. This means all future comments of the author will autmomatically be rejected. Only valid in combination with STATUS_REJECTED.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if moderationStatus is not None:
            kwargs['moderationStatus'] = moderationStatus
        if banAuthor is not None:
            kwargs['banAuthor'] = banAuthor

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.comments().setModerationStatus(**kwargs, body=body) # type: ignore
        else:
            request = self.client.comments().setModerationStatus(**kwargs) # type: ignore
        return request.execute()

    async def comments_mark_as_spam(
        self,
        id: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Expresses the caller's opinion that one or more comments should be flagged as spam.

        HTTP POST youtube/v3/comments/markAsSpam

        Args:
            id (str, required): Flags the comments with the given IDs as spam in the caller's opinion.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.comments().markAsSpam(**kwargs, body=body) # type: ignore
        else:
            request = self.client.comments().markAsSpam(**kwargs) # type: ignore
        return request.execute()

    async def comment_threads_list(
        self,
        part: str,
        id: Optional[str] = None,
        videoId: Optional[str] = None,
        postId: Optional[str] = None,
        channelId: Optional[str] = None,
        allThreadsRelatedToChannelId: Optional[str] = None,
        moderationStatus: Optional[str] = None,
        searchTerms: Optional[str] = None,
        textFormat: Optional[str] = None,
        order: Optional[str] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/commentThreads

        Args:
            id (str, optional): Returns the comment threads with the given IDs for Stubby or Apiary.
            videoId (str, optional): Returns the comment threads of the specified video.
            postId (str, optional): Returns the comment threads of the specified post.
            channelId (str, optional): Returns the comment threads for all the channel comments (ie does not include comments left on videos).
            allThreadsRelatedToChannelId (str, optional): Returns the comment threads of all videos of the channel and the channel comments as well.
            moderationStatus (str, optional): Limits the returned comment threads to those with the specified moderation status. Not compatible with the 'id' filter. Valid values: published, heldForReview, likelySpam.
            searchTerms (str, optional): Limits the returned comment threads to those matching the specified key words. Not compatible with the 'id' filter.
            textFormat (str, optional): The requested text format for the returned comments.
            order (str, optional):
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more commentThread resource properties that the API response will include.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if videoId is not None:
            kwargs['videoId'] = videoId
        if postId is not None:
            kwargs['postId'] = postId
        if channelId is not None:
            kwargs['channelId'] = channelId
        if allThreadsRelatedToChannelId is not None:
            kwargs['allThreadsRelatedToChannelId'] = allThreadsRelatedToChannelId
        if moderationStatus is not None:
            kwargs['moderationStatus'] = moderationStatus
        if searchTerms is not None:
            kwargs['searchTerms'] = searchTerms
        if textFormat is not None:
            kwargs['textFormat'] = textFormat
        if order is not None:
            kwargs['order'] = order
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part

        request = self.client.commentThreads().list(**kwargs) # type: ignore
        return request.execute()

    async def comment_threads_insert(
        self,
        part: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/commentThreads

        Args:
            part (str, required): The *part* parameter identifies the properties that the API response will include. Set the parameter value to snippet. The snippet part has a quota cost of 2 units.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.commentThreads().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.commentThreads().insert(**kwargs) # type: ignore
        return request.execute()

    async def youtube_v3_update_comment_threads(
        self,
        part: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Updates an existing resource.

        HTTP PUT youtube/v3/commentThreads

        Args:
            part (str, optional): The *part* parameter specifies a comma-separated list of commentThread resource properties that the API response will include. You must at least include the snippet part in the parameter value since that part contains all of the properties that the API request can update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.youtube_v3().updateCommentThreads(**kwargs, body=body) # type: ignore
        else:
            request = self.client.youtube_v3().updateCommentThreads(**kwargs) # type: ignore
        return request.execute()

    async def youtube_v3_live_chat_messages_stream(
        self,
        liveChatId: Optional[str] = None,
        hl: Optional[str] = None,
        profileImageSize: Optional[int] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        part: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Allows a user to load live chat through a server-streamed RPC.

        HTTP GET youtube/v3/liveChat/messages/stream

        Args:
            liveChatId (str, optional): The id of the live chat for which comments should be returned.
            hl (str, optional): Specifies the localization language in which the system messages should be returned.
            profileImageSize (int, optional): Specifies the size of the profile image that should be returned for each user.
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set. Not used in the streaming RPC.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken property identify other pages that could be retrieved.
            part (str, optional): The *part* parameter specifies the liveChatComment resource parts that the API response will include. Supported values are id, snippet, and authorDetails.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if liveChatId is not None:
            kwargs['liveChatId'] = liveChatId
        if hl is not None:
            kwargs['hl'] = hl
        if profileImageSize is not None:
            kwargs['profileImageSize'] = profileImageSize
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part

        request = self.client.youtube_v3_liveChat_messages().stream(**kwargs) # type: ignore
        return request.execute()

    async def i18n_languages_list(
        self,
        part: str,
        hl: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/i18nLanguages

        Args:
            hl (str, optional):
            part (str, required): The *part* parameter specifies the i18nLanguage resource properties that the API response will include. Set the parameter value to snippet.
        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if hl is not None:
            kwargs['hl'] = hl
        if part is not None:
            kwargs['part'] = part

        request = self.client.i18nLanguages().list(**kwargs) # type: ignore
        return request.execute()

    async def i18n_regions_list(
        self,
        part: str,
        hl: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/i18nRegions

        Args:
            hl (str, optional):
            part (str, required): The *part* parameter specifies the i18nRegion resource properties that the API response will include. Set the parameter value to snippet.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if hl is not None:
            kwargs['hl'] = hl
        if part is not None:
            kwargs['part'] = part

        request = self.client.i18nRegions().list(**kwargs) # type: ignore
        return request.execute()

    async def live_broadcasts_list(
        self,
        part: str,
        broadcastStatus: Optional[str] = None,
        id: Optional[str] = None,
        mine: Optional[bool] = None,
        broadcastType: Optional[str] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieve the list of broadcasts associated with the given channel.

        HTTP GET youtube/v3/liveBroadcasts

        Args:
            broadcastStatus (str, optional): Return broadcasts with a certain status, e.g. active broadcasts.
            id (str, optional): Return broadcasts with the given ids from Stubby or Apiary.
            mine (bool, optional):
            broadcastType (str, optional): Return only broadcasts with the selected type.
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more liveBroadcast resource properties that the API response will include. The part names that you can include in the parameter value are id, snippet, contentDetails, status and statistics.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if broadcastStatus is not None:
            kwargs['broadcastStatus'] = broadcastStatus
        if id is not None:
            kwargs['id'] = id
        if mine is not None:
            kwargs['mine'] = mine
        if broadcastType is not None:
            kwargs['broadcastType'] = broadcastType
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        request = self.client.liveBroadcasts().list(**kwargs) # type: ignore
        return request.execute()

    async def live_broadcasts_insert(
        self,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new stream for the authenticated user.

        HTTP POST youtube/v3/liveBroadcasts

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include. The part properties that you can include in the parameter value are id, snippet, contentDetails, and status.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.liveBroadcasts().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.liveBroadcasts().insert(**kwargs) # type: ignore
        return request.execute()

    async def live_broadcasts_update(
        self,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Updates an existing broadcast for the authenticated user.

        HTTP PUT youtube/v3/liveBroadcasts

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include. The part properties that you can include in the parameter value are id, snippet, contentDetails, and status. Note that this method will override the existing values for all of the mutable properties that are contained in any parts that the parameter value specifies. For example, a broadcast's privacy status is defined in the status part. As such, if your request is updating a private or unlisted broadcast, and the request's part parameter value includes the status part, the broadcast's privacy setting will be updated to whatever value the request body specifies. If the request body does not specify a value, the existing privacy setting will be removed and the broadcast will revert to the default privacy setting.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.liveBroadcasts().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.liveBroadcasts().update(**kwargs) # type: ignore
        return request.execute()

    async def live_broadcasts_delete(
        self,
        id: str,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Delete a given broadcast.

        HTTP DELETE youtube/v3/liveBroadcasts

        Args:
            id (str, required): Broadcast to delete.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        request = self.client.liveBroadcasts().delete(**kwargs) # type: ignore
        return request.execute()

    async def live_broadcasts_bind(
        self,
        id: str,
        part: str,
        streamId: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Bind a broadcast to a stream.

        HTTP POST youtube/v3/liveBroadcasts/bind

        Args:
            id (str, required): Broadcast to bind to the stream
            streamId (str, optional): Stream to bind, if not set unbind the current one.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more liveBroadcast resource properties that the API response will include. The part names that you can include in the parameter value are id, snippet, contentDetails, and status.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if streamId is not None:
            kwargs['streamId'] = streamId
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.liveBroadcasts().bind(**kwargs, body=body) # type: ignore
        else:
            request = self.client.liveBroadcasts().bind(**kwargs) # type: ignore
        return request.execute()

    async def live_broadcasts_transition(
        self,
        id: str,
        broadcastStatus: str,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Transition a broadcast to a given status.

        HTTP POST youtube/v3/liveBroadcasts/transition

        Args:
            id (str, required): Broadcast to transition.
            broadcastStatus (str, required): The status to which the broadcast is going to transition.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more liveBroadcast resource properties that the API response will include. The part names that you can include in the parameter value are id, snippet, contentDetails, and status.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if broadcastStatus is not None:
            kwargs['broadcastStatus'] = broadcastStatus
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.liveBroadcasts().transition(**kwargs, body=body) # type: ignore
        else:
            request = self.client.liveBroadcasts().transition(**kwargs) # type: ignore
        return request.execute()

    async def live_broadcasts_insert_cuepoint(
        self,
        id: Optional[str] = None,
        part: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Insert cuepoints in a broadcast

        HTTP POST youtube/v3/liveBroadcasts/cuepoint

        Args:
            id (str, optional): Broadcast to insert ads to, or equivalently `external_video_id` for internal use.
            part (str, optional): The *part* parameter specifies a comma-separated list of one or more liveBroadcast resource properties that the API response will include. The part names that you can include in the parameter value are id, snippet, contentDetails, and status.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.liveBroadcasts().insertCuepoint(**kwargs, body=body) # type: ignore
        else:
            request = self.client.liveBroadcasts().insertCuepoint(**kwargs) # type: ignore
        return request.execute()

    async def live_chat_bans_insert(
        self,
        part: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/liveChat/bans

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response returns. Set the parameter value to snippet.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.liveChatBans().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.liveChatBans().insert(**kwargs) # type: ignore
        return request.execute()

    async def live_chat_bans_delete(
        self,
        id: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes a chat ban.

        HTTP DELETE youtube/v3/liveChat/bans

        Args:
            id (str, required):
        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id

        request = self.client.liveChatBans().delete(**kwargs) # type: ignore
        return request.execute()

    async def live_chat_messages_list(
        self,
        liveChatId: str,
        part: str,
        hl: Optional[str] = None,
        profileImageSize: Optional[int] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/liveChat/messages

        Args:
            liveChatId (str, required): The id of the live chat for which comments should be returned.
            hl (str, optional): Specifies the localization language in which the system messages should be returned.
            profileImageSize (int, optional): Specifies the size of the profile image that should be returned for each user.
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set. Not used in the streaming RPC.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken property identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies the liveChatComment resource parts that the API response will include. Supported values are id, snippet, and authorDetails.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if liveChatId is not None:
            kwargs['liveChatId'] = liveChatId
        if hl is not None:
            kwargs['hl'] = hl
        if profileImageSize is not None:
            kwargs['profileImageSize'] = profileImageSize
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part

        request = self.client.liveChatMessages().list(**kwargs) # type: ignore
        return request.execute()

    async def live_chat_messages_insert(
        self,
        part: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/liveChat/messages

        Args:
            part (str, required): The *part* parameter serves two purposes. It identifies the properties that the write operation will set as well as the properties that the API response will include. Set the parameter value to snippet.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.liveChatMessages().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.liveChatMessages().insert(**kwargs) # type: ignore
        return request.execute()

    async def live_chat_messages_delete(
        self,
        id: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes a chat message.

        HTTP DELETE youtube/v3/liveChat/messages

        Args:
            id (str, required):
        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id

        request = self.client.liveChatMessages().delete(**kwargs) # type: ignore
        return request.execute()

    async def live_chat_messages_transition(
        self,
        id: Optional[str] = None,
        status: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Transition a durable chat event.

        HTTP POST youtube/v3/liveChat/messages/transition

        Args:
            id (str, optional): The ID that uniquely identify the chat message event to transition.
            status (str, optional): The status to which the chat event is going to transition.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if status is not None:
            kwargs['status'] = status

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.liveChatMessages().transition(**kwargs, body=body) # type: ignore
        else:
            request = self.client.liveChatMessages().transition(**kwargs) # type: ignore
        return request.execute()

    async def live_chat_moderators_list(
        self,
        liveChatId: str,
        part: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/liveChat/moderators

        Args:
            liveChatId (str, required): The id of the live chat for which moderators should be returned.
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies the liveChatModerator resource parts that the API response will include. Supported values are id and snippet.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if liveChatId is not None:
            kwargs['liveChatId'] = liveChatId
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part

        request = self.client.liveChatModerators().list(**kwargs) # type: ignore
        return request.execute()

    async def live_chat_moderators_insert(
        self,
        part: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/liveChat/moderators

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response returns. Set the parameter value to snippet.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.liveChatModerators().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.liveChatModerators().insert(**kwargs) # type: ignore
        return request.execute()

    async def live_chat_moderators_delete(
        self,
        id: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes a chat moderator.

        HTTP DELETE youtube/v3/liveChat/moderators

        Args:
            id (str, required):
        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id

        request = self.client.liveChatModerators().delete(**kwargs) # type: ignore
        return request.execute()

    async def live_streams_list(
        self,
        part: str,
        id: Optional[str] = None,
        mine: Optional[bool] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieve the list of streams associated with the given channel. --

        HTTP GET youtube/v3/liveStreams

        Args:
            id (str, optional): Return LiveStreams with the given ids from Stubby or Apiary.
            mine (bool, optional):
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more liveStream resource properties that the API response will include. The part names that you can include in the parameter value are id, snippet, cdn, and status.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if mine is not None:
            kwargs['mine'] = mine
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        request = self.client.liveStreams().list(**kwargs) # type: ignore
        return request.execute()

    async def live_streams_insert(
        self,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new stream for the authenticated user.

        HTTP POST youtube/v3/liveStreams

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include. The part properties that you can include in the parameter value are id, snippet, cdn, content_details, and status.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.liveStreams().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.liveStreams().insert(**kwargs) # type: ignore
        return request.execute()

    async def live_streams_update(
        self,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Updates an existing stream for the authenticated user.

        HTTP PUT youtube/v3/liveStreams

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include. The part properties that you can include in the parameter value are id, snippet, cdn, and status. Note that this method will override the existing values for all of the mutable properties that are contained in any parts that the parameter value specifies. If the request body does not specify a value for a mutable property, the existing value for that property will be removed.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.liveStreams().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.liveStreams().update(**kwargs) # type: ignore
        return request.execute()

    async def live_streams_delete(
        self,
        id: str,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes an existing stream for the authenticated user.

        HTTP DELETE youtube/v3/liveStreams

        Args:
            id (str, required):
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        request = self.client.liveStreams().delete(**kwargs) # type: ignore
        return request.execute()

    async def members_list(
        self,
        part: str,
        mode: Optional[str] = None,
        hasAccessToLevel: Optional[str] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        filterByMemberChannelId: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of members that match the request criteria for a channel.

        HTTP GET youtube/v3/members

        Args:
            mode (str, optional): Parameter that specifies which channel members to return.
            hasAccessToLevel (str, optional): Filter members in the results set to the ones that have access to a level.
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            filterByMemberChannelId (str, optional): Comma separated list of channel IDs. Only data about members that are part of this list will be included in the response.
            part (str, required): The *part* parameter specifies the member resource parts that the API response will include. Set the parameter value to snippet.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if mode is not None:
            kwargs['mode'] = mode
        if hasAccessToLevel is not None:
            kwargs['hasAccessToLevel'] = hasAccessToLevel
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if filterByMemberChannelId is not None:
            kwargs['filterByMemberChannelId'] = filterByMemberChannelId
        if part is not None:
            kwargs['part'] = part

        request = self.client.members().list(**kwargs) # type: ignore
        return request.execute()

    async def memberships_levels_list(
        self,
        part: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of all pricing levels offered by a creator to the fans.

        HTTP GET youtube/v3/membershipsLevels

        Args:
            part (str, required): The *part* parameter specifies the membershipsLevel resource parts that the API response will include. Supported values are id and snippet.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part

        request = self.client.membershipsLevels().list(**kwargs) # type: ignore
        return request.execute()

    async def playlists_list(
        self,
        part: str,
        id: Optional[str] = None,
        mine: Optional[bool] = None,
        channelId: Optional[str] = None,
        hl: Optional[str] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/playlists

        Args:
            id (str, optional): Return the playlists with the given IDs for Stubby or Apiary.
            mine (bool, optional): Return the playlists owned by the authenticated user.
            channelId (str, optional): Return the playlists owned by the specified channel ID.
            hl (str, optional): Return content in specified language
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more playlist resource properties that the API response will include. If the parameter identifies a property that contains child properties, the child properties will be included in the response. For example, in a playlist resource, the snippet property contains properties like author, title, description, tags, and timeCreated. As such, if you set *part=snippet*, the API response will contain all of those properties.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if mine is not None:
            kwargs['mine'] = mine
        if channelId is not None:
            kwargs['channelId'] = channelId
        if hl is not None:
            kwargs['hl'] = hl
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        request = self.client.playlists().list(**kwargs) # type: ignore
        return request.execute()

    async def playlists_insert(
        self,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/playlists

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.playlists().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.playlists().insert(**kwargs) # type: ignore
        return request.execute()

    async def playlists_update(
        self,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Updates an existing resource.

        HTTP PUT youtube/v3/playlists

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include. Note that this method will override the existing values for mutable properties that are contained in any parts that the request body specifies. For example, a playlist's description is contained in the snippet part, which must be included in the request body. If the request does not specify a value for the snippet.description property, the playlist's existing description will be deleted.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.playlists().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.playlists().update(**kwargs) # type: ignore
        return request.execute()

    async def playlists_delete(
        self,
        id: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes a resource.

        HTTP DELETE youtube/v3/playlists

        Args:
            id (str, required):
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.playlists().delete(**kwargs) # type: ignore
        return request.execute()

    async def playlist_items_list(
        self,
        part: str,
        id: Optional[str] = None,
        playlistId: Optional[str] = None,
        videoId: Optional[str] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/playlistItems

        Args:
            id (str, optional):
            playlistId (str, optional): Return the playlist items within the given playlist.
            videoId (str, optional): Return the playlist items associated with the given video ID.
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more playlistItem resource properties that the API response will include. If the parameter identifies a property that contains child properties, the child properties will be included in the response. For example, in a playlistItem resource, the snippet property contains numerous fields, including the title, description, position, and resourceId properties. As such, if you set *part=snippet*, the API response will contain all of those properties.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if playlistId is not None:
            kwargs['playlistId'] = playlistId
        if videoId is not None:
            kwargs['videoId'] = videoId
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.playlistItems().list(**kwargs) # type: ignore
        return request.execute()

    async def playlist_items_delete(
        self,
        id: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes a resource.

        HTTP DELETE youtube/v3/playlistItems

        Args:
            id (str, required):
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.playlistItems().delete(**kwargs) # type: ignore
        return request.execute()

    async def playlist_items_insert(
        self,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/playlistItems

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.playlistItems().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.playlistItems().insert(**kwargs) # type: ignore
        return request.execute()

    async def playlist_items_update(
        self,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Updates an existing resource.

        HTTP PUT youtube/v3/playlistItems

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include. Note that this method will override the existing values for all of the mutable properties that are contained in any parts that the parameter value specifies. For example, a playlist item can specify a start time and end time, which identify the times portion of the video that should play when users watch the video in the playlist. If your request is updating a playlist item that sets these values, and the request's part parameter value includes the contentDetails part, the playlist item's start and end times will be updated to whatever value the request body specifies. If the request body does not specify values, the existing start and end times will be removed and replaced with the default settings.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.playlistItems().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.playlistItems().update(**kwargs) # type: ignore
        return request.execute()

    async def playlist_images_list(
        self,
        parent: Optional[str] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        part: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/playlistImages

        Args:
            parent (str, optional): Return PlaylistImages for this playlist id.
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, optional): The *part* parameter specifies a comma-separated list of one or more playlistImage resource properties that the API response will include. If the parameter identifies a property that contains child properties, the child properties will be included in the response.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if parent is not None:
            kwargs['parent'] = parent
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        request = self.client.playlistImages().list(**kwargs) # type: ignore
        return request.execute()

    async def playlist_images_insert(
        self,
        part: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/playlistImages

        Args:
            part (str, optional): The *part* parameter specifies the properties that the API response will include.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.playlistImages().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.playlistImages().insert(**kwargs) # type: ignore
        return request.execute()

    async def playlist_images_update(
        self,
        part: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Updates an existing resource.

        HTTP PUT youtube/v3/playlistImages

        Args:
            part (str, optional): The *part* parameter specifies the properties that the API response will include.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.playlistImages().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.playlistImages().update(**kwargs) # type: ignore
        return request.execute()

    async def playlist_images_delete(
        self,
        id: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes a resource.

        HTTP DELETE youtube/v3/playlistImages

        Args:
            id (str, optional): Id to identify this image. This is returned from by the List method.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.playlistImages().delete(**kwargs) # type: ignore
        return request.execute()

    async def search_list(
        self,
        part: str,
        q: Optional[str] = None,
        type: Optional[str] = None,
        order: Optional[str] = None,
        relevanceLanguage: Optional[str] = None,
        videoDimension: Optional[str] = None,
        videoDefinition: Optional[str] = None,
        videoLicense: Optional[str] = None,
        videoDuration: Optional[str] = None,
        videoCaption: Optional[str] = None,
        videoEmbeddable: Optional[str] = None,
        videoSyndicated: Optional[str] = None,
        videoCategoryId: Optional[str] = None,
        videoType: Optional[str] = None,
        eventType: Optional[str] = None,
        location: Optional[str] = None,
        locationRadius: Optional[str] = None,
        channelId: Optional[str] = None,
        publishedAfter: Optional[str] = None,
        publishedBefore: Optional[str] = None,
        topicId: Optional[str] = None,
        videoPaidProductPlacement: Optional[str] = None,
        forContentOwner: Optional[bool] = None,
        regionCode: Optional[str] = None,
        channelType: Optional[str] = None,
        safeSearch: Optional[str] = None,
        forMine: Optional[bool] = None,
        forDeveloper: Optional[bool] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of search resources

        HTTP GET youtube/v3/search

        Args:
            q (str, optional): Textual search terms to match.
            type (str, optional): Restrict results to a particular set of resource types from One Platform.
            order (str, optional): Sort order of the results.
            relevanceLanguage (str, optional): Return results relevant to this language.
            videoDimension (str, optional): Filter on 3d videos.
            videoDefinition (str, optional): Filter on the definition of the videos.
            videoLicense (str, optional): Filter on the license of the videos.
            videoDuration (str, optional): Filter on the duration of the videos.
            videoCaption (str, optional): Filter on the presence of captions on the videos.
            videoEmbeddable (str, optional): Filter on embeddable videos.
            videoSyndicated (str, optional): Filter on syndicated videos.
            videoCategoryId (str, optional): Filter on videos in a specific category.
            videoType (str, optional): Filter on videos of a specific type.
            eventType (str, optional): Filter on the livestream status of the videos.
            location (str, optional): Filter on location of the video
            locationRadius (str, optional): Filter on distance from the location (specified above).
            channelId (str, optional): Filter on resources belonging to this channelId.
            publishedAfter (str, optional): Filter on resources published after this date.
            publishedBefore (str, optional): Filter on resources published before this date.
            topicId (str, optional): Restrict results to a particular topic.
            videoPaidProductPlacement (str, optional):
            forContentOwner (bool, optional): Search owned by a content owner.
            regionCode (str, optional): Display the content as seen by viewers in this country.
            channelType (str, optional): Add a filter on the channel search.
            safeSearch (str, optional): Indicates whether the search results should include restricted content as well as standard content.
            forMine (bool, optional): Search for the private videos of the authenticated user.
            forDeveloper (bool, optional): Restrict the search to only retrieve videos uploaded using the project id of the authenticated user.
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more search resource properties that the API response will include. Set the parameter value to snippet.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if q is not None:
            kwargs['q'] = q
        if type is not None:
            kwargs['type'] = type
        if order is not None:
            kwargs['order'] = order
        if relevanceLanguage is not None:
            kwargs['relevanceLanguage'] = relevanceLanguage
        if videoDimension is not None:
            kwargs['videoDimension'] = videoDimension
        if videoDefinition is not None:
            kwargs['videoDefinition'] = videoDefinition
        if videoLicense is not None:
            kwargs['videoLicense'] = videoLicense
        if videoDuration is not None:
            kwargs['videoDuration'] = videoDuration
        if videoCaption is not None:
            kwargs['videoCaption'] = videoCaption
        if videoEmbeddable is not None:
            kwargs['videoEmbeddable'] = videoEmbeddable
        if videoSyndicated is not None:
            kwargs['videoSyndicated'] = videoSyndicated
        if videoCategoryId is not None:
            kwargs['videoCategoryId'] = videoCategoryId
        if videoType is not None:
            kwargs['videoType'] = videoType
        if eventType is not None:
            kwargs['eventType'] = eventType
        if location is not None:
            kwargs['location'] = location
        if locationRadius is not None:
            kwargs['locationRadius'] = locationRadius
        if channelId is not None:
            kwargs['channelId'] = channelId
        if publishedAfter is not None:
            kwargs['publishedAfter'] = publishedAfter
        if publishedBefore is not None:
            kwargs['publishedBefore'] = publishedBefore
        if topicId is not None:
            kwargs['topicId'] = topicId
        if videoPaidProductPlacement is not None:
            kwargs['videoPaidProductPlacement'] = videoPaidProductPlacement
        if forContentOwner is not None:
            kwargs['forContentOwner'] = forContentOwner
        if regionCode is not None:
            kwargs['regionCode'] = regionCode
        if channelType is not None:
            kwargs['channelType'] = channelType
        if safeSearch is not None:
            kwargs['safeSearch'] = safeSearch
        if forMine is not None:
            kwargs['forMine'] = forMine
        if forDeveloper is not None:
            kwargs['forDeveloper'] = forDeveloper
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.search().list(**kwargs) # type: ignore
        return request.execute()

    async def subscriptions_list(
        self,
        part: str,
        id: Optional[str] = None,
        mine: Optional[bool] = None,
        channelId: Optional[str] = None,
        forChannelId: Optional[str] = None,
        order: Optional[str] = None,
        mySubscribers: Optional[bool] = None,
        myRecentSubscribers: Optional[bool] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/subscriptions

        Args:
            id (str, optional): Return the subscriptions with the given IDs for Stubby or Apiary.
            mine (bool, optional): Flag for returning the subscriptions of the authenticated user.
            channelId (str, optional): Return the subscriptions of the given channel owner.
            forChannelId (str, optional): Return the subscriptions to the subset of these channels that the authenticated user is subscribed to.
            order (str, optional): The order of the returned subscriptions
            mySubscribers (bool, optional): Return the subscribers of the given channel owner.
            myRecentSubscribers (bool, optional):
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more subscription resource properties that the API response will include. If the parameter identifies a property that contains child properties, the child properties will be included in the response. For example, in a subscription resource, the snippet property contains other properties, such as a display title for the subscription. If you set *part=snippet*, the API response will also contain all of those nested properties.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if mine is not None:
            kwargs['mine'] = mine
        if channelId is not None:
            kwargs['channelId'] = channelId
        if forChannelId is not None:
            kwargs['forChannelId'] = forChannelId
        if order is not None:
            kwargs['order'] = order
        if mySubscribers is not None:
            kwargs['mySubscribers'] = mySubscribers
        if myRecentSubscribers is not None:
            kwargs['myRecentSubscribers'] = myRecentSubscribers
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        request = self.client.subscriptions().list(**kwargs) # type: ignore
        return request.execute()

    async def subscriptions_delete(
        self,
        id: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes a resource.

        HTTP DELETE youtube/v3/subscriptions

        Args:
            id (str, required):
        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id

        request = self.client.subscriptions().delete(**kwargs) # type: ignore
        return request.execute()

    async def subscriptions_insert(
        self,
        part: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/subscriptions

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.subscriptions().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.subscriptions().insert(**kwargs) # type: ignore
        return request.execute()

    async def super_chat_events_list(
        self,
        part: str,
        hl: Optional[str] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/superChatEvents

        Args:
            hl (str, optional): Return rendered funding amounts in specified language.
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved.
            part (str, required): The *part* parameter specifies the superChatEvent resource parts that the API response will include. This parameter is currently not supported.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if hl is not None:
            kwargs['hl'] = hl
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part

        request = self.client.superChatEvents().list(**kwargs) # type: ignore
        return request.execute()

    async def tests_insert(
        self,
        part: str,
        externalChannelId: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: POST method.

        HTTP POST youtube/v3/tests

        Args:
            part (str, required):
            externalChannelId (str, optional):
        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if externalChannelId is not None:
            kwargs['externalChannelId'] = externalChannelId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.tests().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.tests().insert(**kwargs) # type: ignore
        return request.execute()

    async def third_party_links_list(
        self,
        part: str,
        linkingToken: Optional[str] = None,
        type: Optional[str] = None,
        externalChannelId: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/thirdPartyLinks

        Args:
            linkingToken (str, optional): Get a third party link with the given linking token.
            type (str, optional): Get a third party link of the given type.
            externalChannelId (str, optional): Channel ID to which changes should be applied, for delegation.
            part (str, required): The *part* parameter specifies the thirdPartyLink resource parts that the API response will include. Supported values are linkingToken, status, and snippet.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if linkingToken is not None:
            kwargs['linkingToken'] = linkingToken
        if type is not None:
            kwargs['type'] = type
        if externalChannelId is not None:
            kwargs['externalChannelId'] = externalChannelId
        if part is not None:
            kwargs['part'] = part

        request = self.client.thirdPartyLinks().list(**kwargs) # type: ignore
        return request.execute()

    async def third_party_links_insert(
        self,
        part: str,
        externalChannelId: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/thirdPartyLinks

        Args:
            externalChannelId (str, optional): Channel ID to which changes should be applied, for delegation.
            part (str, required): The *part* parameter specifies the thirdPartyLink resource parts that the API request and response will include. Supported values are linkingToken, status, and snippet.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if externalChannelId is not None:
            kwargs['externalChannelId'] = externalChannelId
        if part is not None:
            kwargs['part'] = part

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.thirdPartyLinks().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.thirdPartyLinks().insert(**kwargs) # type: ignore
        return request.execute()

    async def third_party_links_update(
        self,
        part: str,
        externalChannelId: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Updates an existing resource.

        HTTP PUT youtube/v3/thirdPartyLinks

        Args:
            externalChannelId (str, optional): Channel ID to which changes should be applied, for delegation.
            part (str, required): The *part* parameter specifies the thirdPartyLink resource parts that the API request and response will include. Supported values are linkingToken, status, and snippet.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if externalChannelId is not None:
            kwargs['externalChannelId'] = externalChannelId
        if part is not None:
            kwargs['part'] = part

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.thirdPartyLinks().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.thirdPartyLinks().update(**kwargs) # type: ignore
        return request.execute()

    async def third_party_links_delete(
        self,
        linkingToken: str,
        type: str,
        externalChannelId: Optional[str] = None,
        part: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes a resource.

        HTTP DELETE youtube/v3/thirdPartyLinks

        Args:
            linkingToken (str, required): Delete the partner links with the given linking token.
            type (str, required): Type of the link to be deleted.
            externalChannelId (str, optional): Channel ID to which changes should be applied, for delegation.
            part (str, optional): Do not use. Required for compatibility.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if linkingToken is not None:
            kwargs['linkingToken'] = linkingToken
        if type is not None:
            kwargs['type'] = type
        if externalChannelId is not None:
            kwargs['externalChannelId'] = externalChannelId
        if part is not None:
            kwargs['part'] = part

        request = self.client.thirdPartyLinks().delete(**kwargs) # type: ignore
        return request.execute()

    async def thumbnails_set(
        self,
        videoId: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: As this is not an insert in a strict sense (it supports uploading/setting of a thumbnail for multiple videos, which doesn't result in creation of a single resource), I use a custom verb here.

        HTTP POST youtube/v3/thumbnails/set

        Args:
            videoId (str, required): Returns the Thumbnail with the given video IDs for Stubby or Apiary.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The actual CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if videoId is not None:
            kwargs['videoId'] = videoId
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.thumbnails().set(**kwargs, body=body) # type: ignore
        else:
            request = self.client.thumbnails().set(**kwargs) # type: ignore
        return request.execute()

    async def videos_list(
        self,
        part: str,
        id: Optional[str] = None,
        myRating: Optional[str] = None,
        chart: Optional[str] = None,
        videoCategoryId: Optional[str] = None,
        regionCode: Optional[str] = None,
        maxWidth: Optional[int] = None,
        maxHeight: Optional[int] = None,
        hl: Optional[str] = None,
        locale: Optional[str] = None,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/videos

        Args:
            id (str, optional): Return videos with the given ids.
            myRating (str, optional): Return videos liked/disliked by the authenticated user. Does not support RateType.RATED_TYPE_NONE.
            chart (str, optional): Return the videos that are in the specified chart.
            videoCategoryId (str, optional): Use chart that is specific to the specified video category
            regionCode (str, optional): Use a chart that is specific to the specified region
            maxWidth (int, optional): Return the player with maximum height specified in
            maxHeight (int, optional):
            hl (str, optional): Stands for "host language". Specifies the localization language of the metadata to be filled into snippet.localized. The field is filled with the default metadata if there is no localization in the specified language. The parameter value must be a language code included in the list returned by the i18nLanguages.list method (e.g. en_US, es_MX).
            locale (str, optional):
            maxResults (int, optional): The *maxResults* parameter specifies the maximum number of items that should be returned in the result set. *Note:* This parameter is supported for use in conjunction with the myRating and chart parameters, but it is not supported for use in conjunction with the id parameter.
            pageToken (str, optional): The *pageToken* parameter identifies a specific page in the result set that should be returned. In an API response, the nextPageToken and prevPageToken properties identify other pages that could be retrieved. *Note:* This parameter is supported for use in conjunction with the myRating and chart parameters, but it is not supported for use in conjunction with the id parameter.
            part (str, required): The *part* parameter specifies a comma-separated list of one or more video resource properties that the API response will include. If the parameter identifies a property that contains child properties, the child properties will be included in the response. For example, in a video resource, the snippet property contains the channelId, title, description, tags, and categoryId properties. As such, if you set *part=snippet*, the API response will contain all of those properties.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if myRating is not None:
            kwargs['myRating'] = myRating
        if chart is not None:
            kwargs['chart'] = chart
        if videoCategoryId is not None:
            kwargs['videoCategoryId'] = videoCategoryId
        if regionCode is not None:
            kwargs['regionCode'] = regionCode
        if maxWidth is not None:
            kwargs['maxWidth'] = maxWidth
        if maxHeight is not None:
            kwargs['maxHeight'] = maxHeight
        if hl is not None:
            kwargs['hl'] = hl
        if locale is not None:
            kwargs['locale'] = locale
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.videos().list(**kwargs) # type: ignore
        return request.execute()

    async def videos_insert(
        self,
        part: str,
        autoLevels: Optional[bool] = None,
        stabilize: Optional[bool] = None,
        notifySubscribers: Optional[bool] = None,
        onBehalfOfContentOwner: Optional[str] = None,
        onBehalfOfContentOwnerChannel: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Inserts a new resource into this collection.

        HTTP POST youtube/v3/videos

        Args:
            autoLevels (bool, optional): Should auto-levels be applied to the upload.
            stabilize (bool, optional): Should stabilize be applied to the upload.
            notifySubscribers (bool, optional): Notify the channel subscribers about the new video. As default, the notification is enabled.
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include. Note that not all parts contain properties that can be set when inserting or updating a video. For example, the statistics object encapsulates statistics that YouTube calculates for a video and does not contain values that you can set or modify. If the parameter value specifies a part that does not contain mutable values, that part will still be included in the API response.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.
            onBehalfOfContentOwnerChannel (str, optional): This parameter can only be used in a properly authorized request. *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwnerChannel* parameter specifies the YouTube channel ID of the channel to which a video is being added. This parameter is required when a request specifies a value for the onBehalfOfContentOwner parameter, and it can only be used in conjunction with that parameter. In addition, the request must be authorized using a CMS account that is linked to the content owner that the onBehalfOfContentOwner parameter specifies. Finally, the channel that the onBehalfOfContentOwnerChannel parameter value specifies must be linked to the content owner that the onBehalfOfContentOwner parameter specifies. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and perform actions on behalf of the channel specified in the parameter value, without having to provide authentication credentials for each separate channel.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if autoLevels is not None:
            kwargs['autoLevels'] = autoLevels
        if stabilize is not None:
            kwargs['stabilize'] = stabilize
        if notifySubscribers is not None:
            kwargs['notifySubscribers'] = notifySubscribers
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner
        if onBehalfOfContentOwnerChannel is not None:
            kwargs['onBehalfOfContentOwnerChannel'] = onBehalfOfContentOwnerChannel

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.videos().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.videos().insert(**kwargs) # type: ignore
        return request.execute()

    async def videos_update(
        self,
        part: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Updates an existing resource.

        HTTP PUT youtube/v3/videos

        Args:
            part (str, required): The *part* parameter serves two purposes in this operation. It identifies the properties that the write operation will set as well as the properties that the API response will include. Note that this method will override the existing values for all of the mutable properties that are contained in any parts that the parameter value specifies. For example, a video's privacy setting is contained in the status part. As such, if your request is updating a private video, and the request's part parameter value includes the status part, the video's privacy setting will be updated to whatever value the request body specifies. If the request body does not specify a value, the existing privacy setting will be removed and the video will revert to the default privacy setting. In addition, not all parts contain properties that can be set when inserting or updating a video. For example, the statistics object encapsulates statistics that YouTube calculates for a video and does not contain values that you can set or modify. If the parameter value specifies a part that does not contain mutable values, that part will still be included in the API response.
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The actual CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if part is not None:
            kwargs['part'] = part
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.videos().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.videos().update(**kwargs) # type: ignore
        return request.execute()

    async def videos_delete(
        self,
        id: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Deletes a resource.

        HTTP DELETE youtube/v3/videos

        Args:
            id (str, required):
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The actual CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.videos().delete(**kwargs) # type: ignore
        return request.execute()

    async def videos_report_abuse(
        self,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Report abuse for a video.

        HTTP POST youtube/v3/videos/reportAbuse

        Args:
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.videos().reportAbuse(**kwargs, body=body) # type: ignore
        else:
            request = self.client.videos().reportAbuse(**kwargs) # type: ignore
        return request.execute()

    async def videos_rate(
        self,
        id: str,
        rating: str
    ) -> Dict[str, Any]:
        """YouTube Data API: Adds a like or dislike rating to a video or removes a rating from a video.

        HTTP POST youtube/v3/videos/rate

        Args:
            id (str, required):
            rating (str, required):
        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if rating is not None:
            kwargs['rating'] = rating

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.videos().rate(**kwargs, body=body) # type: ignore
        else:
            request = self.client.videos().rate(**kwargs) # type: ignore
        return request.execute()

    async def videos_get_rating(
        self,
        id: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves the ratings that the authorized user gave to a list of specified videos.

        HTTP GET youtube/v3/videos/getRating

        Args:
            id (str, required):
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        request = self.client.videos().getRating(**kwargs) # type: ignore
        return request.execute()

    async def video_abuse_report_reasons_list(
        self,
        part: str,
        hl: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/videoAbuseReportReasons

        Args:
            hl (str, optional):
            part (str, required): The *part* parameter specifies the videoCategory resource parts that the API response will include. Supported values are id and snippet.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if hl is not None:
            kwargs['hl'] = hl
        if part is not None:
            kwargs['part'] = part

        request = self.client.videoAbuseReportReasons().list(**kwargs) # type: ignore
        return request.execute()

    async def video_categories_list(
        self,
        part: str,
        id: Optional[str] = None,
        regionCode: Optional[str] = None,
        hl: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Retrieves a list of resources, possibly filtered.

        HTTP GET youtube/v3/videoCategories

        Args:
            id (str, optional): Returns the video categories with the given IDs for Stubby or Apiary.
            regionCode (str, optional):
            hl (str, optional):
            part (str, required): The *part* parameter specifies the videoCategory resource properties that the API response will include. Set the parameter value to snippet.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id
        if regionCode is not None:
            kwargs['regionCode'] = regionCode
        if hl is not None:
            kwargs['hl'] = hl
        if part is not None:
            kwargs['part'] = part

        request = self.client.videoCategories().list(**kwargs) # type: ignore
        return request.execute()

    async def video_trainability_get(
        self,
        id: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Returns the trainability status of a video.

        HTTP GET youtube/v3/videoTrainability

        Args:
            id (str, optional): The ID of the video to retrieve.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if id is not None:
            kwargs['id'] = id

        request = self.client.videoTrainability().get(**kwargs) # type: ignore
        return request.execute()

    async def watermarks_set(
        self,
        channelId: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Allows upload of watermark image and setting it for a channel.

        HTTP POST youtube/v3/watermarks/set

        Args:
            channelId (str, required):
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if channelId is not None:
            kwargs['channelId'] = channelId
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.watermarks().set(**kwargs, body=body) # type: ignore
        else:
            request = self.client.watermarks().set(**kwargs) # type: ignore
        return request.execute()

    async def watermarks_unset(
        self,
        channelId: str,
        onBehalfOfContentOwner: Optional[str] = None
    ) -> Dict[str, Any]:
        """YouTube Data API: Allows removal of channel watermark.

        HTTP POST youtube/v3/watermarks/unset

        Args:
            channelId (str, required):
            onBehalfOfContentOwner (str, optional): *Note:* This parameter is intended exclusively for YouTube content partners. The *onBehalfOfContentOwner* parameter indicates that the request's authorization credentials identify a YouTube CMS user who is acting on behalf of the content owner specified in the parameter value. This parameter is intended for YouTube content partners that own and manage many different YouTube channels. It allows content owners to authenticate once and get access to all their video and channel data, without having to provide authentication credentials for each individual channel. The CMS account that the user authenticates with must be linked to the specified YouTube content owner.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if channelId is not None:
            kwargs['channelId'] = channelId
        if onBehalfOfContentOwner is not None:
            kwargs['onBehalfOfContentOwner'] = onBehalfOfContentOwner

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.watermarks().unset(**kwargs, body=body) # type: ignore
        else:
            request = self.client.watermarks().unset(**kwargs) # type: ignore
        return request.execute()

    async def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
