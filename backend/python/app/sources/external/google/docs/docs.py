from typing import Any, Dict, Optional


class GoogleDocsDataSource:
    """
    Auto-generated Google Docs API client wrapper.
    Uses Google SDK client internally for all operations.
    This class wraps all Google Docs API v1 methods and provides
    a consistent interface while using the official Google SDK.
    """
    def __init__(
        self,
        client: object
    ) -> None:
        """
        Initialize with Google Docs API client.
        Args:
            client: Google Docs API client from build('docs', 'v1', credentials=credentials)
        """
        self.client = client

    def documents_get(
        self,
        documentId: str,
        suggestionsViewMode: Optional[str] = None,
        includeTabsContent: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Google Docs API: Gets the latest version of the specified document.

        HTTP GET v1/documents/{documentId}

        Args:
            documentId (str, required): The ID of the document to retrieve.
            suggestionsViewMode (str, optional): The suggestions view mode to apply to the document. This allows viewing the document with all suggestions inline, accepted or rejected. If one is not specified, DEFAULT_FOR_CURRENT_ACCESS is used.
            includeTabsContent (bool, optional): Whether to populate the Document.tabs field instead of the text content fields like `body` and `documentStyle` on Document. - When `True`: Document content populates in the Document.tabs field instead of the text content fields in Document. - When `False`: The content of the document's first tab populates the content fields in Document excluding Document.tabs. If a document has only one tab, then that tab is used to populate the document content. Document.tabs will be empty.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if documentId is not None:
            kwargs['documentId'] = documentId
        if suggestionsViewMode is not None:
            kwargs['suggestionsViewMode'] = suggestionsViewMode
        if includeTabsContent is not None:
            kwargs['includeTabsContent'] = includeTabsContent

        request = self.client.documents().get(**kwargs) # type: ignore
        return request.execute()

    def documents_create(self) -> Dict[str, Any]:
        """Google Docs API: Creates a blank document using the title given in the request. Other fields in the request, including any provided content, are ignored. Returns the created document.

        HTTP POST v1/documents

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        # No parameters for this method

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.documents().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.documents().create(**kwargs) # type: ignore
        return request.execute()

    def documents_batch_update(
        self,
        documentId: str
    ) -> Dict[str, Any]:
        """Google Docs API: Applies one or more updates to the document. Each request is validated before being applied. If any request is not valid, then the entire request will fail and nothing will be applied. Some requests have replies to give you some information about how they are applied. Other requests do not need to return information; these each return an empty reply. The order of replies matches that of the requests. For example, suppose you call batchUpdate with four updates, and only the third one returns information. The response would have two empty replies, the reply to the third request, and another empty reply, in that order. Because other users may be editing the document, the document might not exactly reflect your changes: your changes may be altered with respect to collaborator changes. If there are no collaborators, the document should reflect your changes. In any case, the updates in your request are guaranteed to be applied together atomically.

        HTTP POST v1/documents/{documentId}:batchUpdate

        Args:
            documentId (str, required): The ID of the document to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if documentId is not None:
            kwargs['documentId'] = documentId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.documents().batchUpdate(**kwargs, body=body) # type: ignore
        else:
            request = self.client.documents().batchUpdate(**kwargs) # type: ignore
        return request.execute()

    def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
