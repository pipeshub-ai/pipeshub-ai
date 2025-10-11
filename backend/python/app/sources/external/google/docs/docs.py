from typing import Any, Dict, Optional

from app.sources.client.google.google import GoogleClient


class GoogleDocsDataSource:
    """
    Auto-generated Google Docs API client wrapper.
    Uses Google SDK client internally for all operations.
    This class wraps all Google Docs API v1 methods and provides
    a consistent interface while using the official Google SDK.
    """
    def __init__(
        self,
        client: GoogleClient
    ) -> None:
        """
        Initialize with Google Docs API client.
        Args:
            client: Google Docs API client from build('docs', 'v1', credentials=credentials)
        """
        self.client = client

    async def documents_get(
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

    async def documents_create(
        self,
        documentId: Optional[str] = None,
        title: Optional[str] = None,
        tabs: Optional[list] = None,
        revisionId: Optional[str] = None,
        suggestionsViewMode: Optional[str] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None,
        footers: Optional[Dict[str, Any]] = None,
        footnotes: Optional[Dict[str, Any]] = None,
        documentStyle: Optional[Dict[str, Any]] = None,
        suggestedDocumentStyleChanges: Optional[Dict[str, Any]] = None,
        namedStyles: Optional[Dict[str, Any]] = None,
        suggestedNamedStylesChanges: Optional[Dict[str, Any]] = None,
        lists: Optional[Dict[str, Any]] = None,
        namedRanges: Optional[Dict[str, Any]] = None,
        inlineObjects: Optional[Dict[str, Any]] = None,
        positionedObjects: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Google Docs API: Creates a blank document using the title given in the request. Other fields in the request, including any provided content, are ignored. Returns the created document.

        HTTP POST v1/documents

        Args:
            documentId (str, optional): The ID of the document.
            title (str, optional): The title of the document.
            tabs (list, optional): The tabs in the document.
            revisionId (str, optional): The revision ID of the document.
            suggestionsViewMode (str, optional): The suggestions view mode to apply to the document.
            body (Dict[str, Any], optional): The body of the document.
            headers (Dict[str, Any], optional): The headers of the document.
            footers (Dict[str, Any], optional): The footers of the document.
            footnotes (Dict[str, Any], optional): The footnotes of the document.
            documentStyle (Dict[str, Any], optional): The document style.
            suggestedDocumentStyleChanges (Dict[str, Any], optional): Suggested document style changes.
            namedStyles (Dict[str, Any], optional): Named styles for the document.
            suggestedNamedStylesChanges (Dict[str, Any], optional): Suggested named styles changes.
            lists (Dict[str, Any], optional): Lists in the document.
            namedRanges (Dict[str, Any], optional): Named ranges in the document.
            inlineObjects (Dict[str, Any], optional): Inline objects in the document.
            positionedObjects (Dict[str, Any], optional): Positioned objects in the document.

        Returns:
            Dict[str, Any]: API response
        """
        request_body = {}
        if documentId is not None:
            request_body['documentId'] = documentId
        if title is not None:
            request_body['title'] = title
        if tabs is not None:
            request_body['tabs'] = tabs
        if revisionId is not None:
            request_body['revisionId'] = revisionId
        if suggestionsViewMode is not None:
            request_body['suggestionsViewMode'] = suggestionsViewMode
        if body is not None:
            request_body['body'] = body
        if headers is not None:
            request_body['headers'] = headers
        if footers is not None:
            request_body['footers'] = footers
        if footnotes is not None:
            request_body['footnotes'] = footnotes
        if documentStyle is not None:
            request_body['documentStyle'] = documentStyle
        if suggestedDocumentStyleChanges is not None:
            request_body['suggestedDocumentStyleChanges'] = suggestedDocumentStyleChanges
        if namedStyles is not None:
            request_body['namedStyles'] = namedStyles
        if suggestedNamedStylesChanges is not None:
            request_body['suggestedNamedStylesChanges'] = suggestedNamedStylesChanges
        if lists is not None:
            request_body['lists'] = lists
        if namedRanges is not None:
            request_body['namedRanges'] = namedRanges
        if inlineObjects is not None:
            request_body['inlineObjects'] = inlineObjects
        if positionedObjects is not None:
            request_body['positionedObjects'] = positionedObjects

        request = self.client.documents().create(body=request_body) # type: ignore
        return request.execute()

    async def documents_batch_update(
        self,
        documentId: str,
        requests: Optional[list] = None,
        writeControl: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Docs API: Applies one or more updates to the document. Each request is validated before being applied. If any request is not valid, then the entire request will fail and nothing will be applied. Some requests have replies to give you some information about how they are applied. Other requests do not need to return information; these each return an empty reply. The order of replies matches that of the requests. For example, suppose you call batchUpdate with four updates, and only the third one returns information. The response would have two empty replies, the reply to the third request, and another empty reply, in that order. Because other users may be editing the document, the document might not exactly reflect your changes: your changes may be altered with respect to collaborator changes. If there are no collaborators, the document should reflect your changes. In any case, the updates in your request are guaranteed to be applied together atomically.

        HTTP POST v1/documents/{documentId}:batchUpdate

        Args:
            documentId (str, required): The ID of the document to update.
            requests (list, optional): List of update requests. Each request can be one of:
                - replaceAllText: ReplaceAllTextRequest
                - insertText: InsertTextRequest
                - updateTextStyle: UpdateTextStyleRequest
                - createParagraphBullets: CreateParagraphBulletsRequest
                - deleteParagraphBullets: DeleteParagraphBulletsRequest
                - createNamedRange: CreateNamedRangeRequest
                - deleteNamedRange: DeleteNamedRangeRequest
                - updateParagraphStyle: UpdateParagraphStyleRequest
                - deleteContentRange: DeleteContentRangeRequest
                - insertInlineImage: InsertInlineImageRequest
                - insertTable: InsertTableRequest
                - insertTableRow: InsertTableRowRequest
                - insertTableColumn: InsertTableColumnRequest
                - deleteTableRow: DeleteTableRowRequest
                - deleteTableColumn: DeleteTableColumnRequest
                - insertPageBreak: InsertPageBreakRequest
                - deletePositionedObject: DeletePositionedObjectRequest
                - updateTableColumnProperties: UpdateTableColumnPropertiesRequest
                - updateTableCellStyle: UpdateTableCellStyleRequest
                - updateTableRowStyle: UpdateTableRowStyleRequest
                - replaceImage: ReplaceImageRequest
                - updateDocumentStyle: UpdateDocumentStyleRequest
                - mergeTableCells: MergeTableCellsRequest
                - unmergeTableCells: UnmergeTableCellsRequest
                - createHeader: CreateHeaderRequest
                - createFooter: CreateFooterRequest
                - createFootnote: CreateFootnoteRequest
                - replaceNamedRangeContent: ReplaceNamedRangeContentRequest
                - updateSectionStyle: UpdateSectionStyleRequest
                - insertSectionBreak: InsertSectionBreakRequest
                - deleteHeader: DeleteHeaderRequest
                - deleteFooter: DeleteFooterRequest
                - pinTableHeaderRows: PinTableHeaderRowsRequest
            writeControl (Dict[str, Any], optional): Controls how the write request is executed.
            **kwargs: Additional arguments to pass to the API request.

        Returns:
            Dict[str, Any]: API response
        """
        request_params = {}
        if documentId is not None:
            request_params['documentId'] = documentId

        request_body = {}
        if requests is not None:
            request_body['requests'] = requests
        if writeControl is not None:
            request_body['writeControl'] = writeControl

        # Add any additional kwargs to request body
        for key, value in kwargs.items():
            if value is not None:
                request_body[key] = value

        request = self.client.documents().batchUpdate(**request_params, body=request_body) # type: ignore
        return request.execute()

    async def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
