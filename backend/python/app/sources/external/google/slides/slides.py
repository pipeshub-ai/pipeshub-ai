from typing import Any, Dict, Optional

from app.sources.client.google.google import GoogleClient


class GoogleSlidesDataSource:
    """
    Auto-generated Google Slides API client wrapper.
    Uses Google SDK client internally for all operations.
    This class wraps all Google Slides API v1 methods and provides
    a consistent interface while using the official Google SDK.
    """
    def __init__(
        self,
        client: GoogleClient
    ) -> None:
        """
        Initialize with Google Slides API client.
        Args:
            client: Google Slides API client from build('slides', 'v1', credentials=credentials)
        """
        self.client = client

    async def presentations_get(
        self,
        presentationId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Slides API: Gets the latest version of the specified presentation.

        HTTP GET v1/presentations/{+presentationId}

        Args:
            presentationId (str, required): The ID of the presentation to retrieve.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if presentationId is not None:
            kwargs['presentationId'] = presentationId

        request = self.client.presentations().get(**kwargs) # type: ignore
        return request.execute()

    async def presentations_create(self, **kwargs) -> Dict[str, Any]:
        """Google Slides API: Creates a blank presentation using the title given in the request. If a `presentationId` is provided, it is used as the ID of the new presentation. Otherwise, a new ID is generated. Other fields in the request, including any provided content, are ignored. Returns the created presentation.

        HTTP POST v1/presentations

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        # No parameters for this method

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.presentations().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.presentations().create(**kwargs) # type: ignore
        return request.execute()

    async def presentations_batch_update(
        self,
        presentationId: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Google Slides API: Applies one or more updates to the presentation. Each request is validated before being applied. If any request is not valid, then the entire request will fail and nothing will be applied. Some requests have replies to give you some information about how they are applied. Other requests do not need to return information; these each return an empty reply. The order of replies matches that of the requests. For example, suppose you call batchUpdate with four updates, and only the third one returns information. The response would have two empty replies: the reply to the third request, and another empty reply, in that order. Because other users may be editing the presentation, the presentation might not exactly reflect your changes: your changes may be altered with respect to collaborator changes. If there are no collaborators, the presentation should reflect your changes. In any case, the updates in your request are guaranteed to be applied together atomically.

        HTTP POST v1/presentations/{presentationId}:batchUpdate

        Args:
            presentationId (str, required): The presentation to apply the updates to.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if presentationId is not None:
            kwargs['presentationId'] = presentationId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.presentations().batchUpdate(**kwargs, body=body) # type: ignore
        else:
            request = self.client.presentations().batchUpdate(**kwargs) # type: ignore
        return request.execute()

    async def presentations_pages_get(
        self,
        presentationId: str,
        pageObjectId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Slides API: Gets the latest version of the specified page in the presentation.

        HTTP GET v1/presentations/{presentationId}/pages/{pageObjectId}

        Args:
            presentationId (str, required): The ID of the presentation to retrieve.
            pageObjectId (str, required): The object ID of the page to retrieve.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if presentationId is not None:
            kwargs['presentationId'] = presentationId
        if pageObjectId is not None:
            kwargs['pageObjectId'] = pageObjectId

        request = self.client.presentations_pages().get(**kwargs) # type: ignore
        return request.execute()

    async def presentations_pages_get_thumbnail(
        self,
        presentationId: str,
        pageObjectId: str,
        thumbnailProperties_mimeType: Optional[str] = None,
        thumbnailProperties_thumbnailSize: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Slides API: Generates a thumbnail of the latest version of the specified page in the presentation and returns a URL to the thumbnail image. This request counts as an [expensive read request](https://developers.google.com/workspace/slides/limits) for quota purposes.

        HTTP GET v1/presentations/{presentationId}/pages/{pageObjectId}/thumbnail

        Args:
            presentationId (str, required): The ID of the presentation to retrieve.
            pageObjectId (str, required): The object ID of the page whose thumbnail to retrieve.
            thumbnailProperties_mimeType (str, optional): The optional mime type of the thumbnail image. If you don't specify the mime type, the mime type defaults to PNG.
            thumbnailProperties_thumbnailSize (str, optional): The optional thumbnail image size. If you don't specify the size, the server chooses a default size of the image.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if presentationId is not None:
            kwargs['presentationId'] = presentationId
        if pageObjectId is not None:
            kwargs['pageObjectId'] = pageObjectId
        if thumbnailProperties_mimeType is not None:
            kwargs['thumbnailProperties.mimeType'] = thumbnailProperties_mimeType
        if thumbnailProperties_thumbnailSize is not None:
            kwargs['thumbnailProperties.thumbnailSize'] = thumbnailProperties_thumbnailSize

        request = self.client.presentations_pages().getThumbnail(**kwargs) # type: ignore
        return request.execute()

    async def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
