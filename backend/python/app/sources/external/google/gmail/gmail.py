from typing import Any, Dict, Optional

from app.sources.client.google.google import GoogleClient


class GoogleGmailDataSource:
    """
    Auto-generated Gmail API client wrapper.
    Uses Google SDK client internally for all operations.
    This class wraps all Gmail API v1 methods and provides
    a consistent interface while using the official Google SDK.
    """
    def __init__(
        self,
        client: GoogleClient
    ) -> None:
        """
        Initialize with Gmail API client.
        Args:
            client: Gmail API client from build('gmail', 'v1', credentials=credentials)
        """
        self.client = client

    async def users_get_profile(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets the current user's Gmail profile.

        HTTP GET gmail/v1/users/{userId}/profile

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        request = self.client.users().getProfile(**kwargs) # type: ignore
        return request.execute()

    async def users_watch(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Set up or update a push notification watch on the given user mailbox.

        HTTP POST gmail/v1/users/{userId}/watch

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().watch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().watch(**kwargs) # type: ignore
        return request.execute()

    async def users_stop(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Stop receiving push notifications for the given user mailbox.

        HTTP POST gmail/v1/users/{userId}/stop

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().stop(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().stop(**kwargs) # type: ignore
        return request.execute()

    async def users_drafts_delete(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Immediately and permanently deletes the specified draft. Does not simply trash it.

        HTTP DELETE gmail/v1/users/{userId}/drafts/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the draft to delete.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        request = self.client.users().drafts().delete(**kwargs) # type: ignore
        return request.execute()

    async def users_drafts_create(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Creates a new draft with the `DRAFT` label.

        HTTP POST gmail/v1/users/{userId}/drafts

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().drafts().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().drafts().create(**kwargs) # type: ignore
        return request.execute()

    async def users_drafts_get(
        self,
        userId: str,
        id: str,
        format: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets the specified draft.

        HTTP GET gmail/v1/users/{userId}/drafts/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the draft to retrieve.
            format (str, optional): The format to return the draft in.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id
        if format is not None:
            kwargs['format'] = format

        request = self.client.users().drafts().get(**kwargs) # type: ignore
        return request.execute()

    async def users_drafts_list(
        self,
        userId: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        q: Optional[str] = None,
        includeSpamTrash: Optional[bool] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Lists the drafts in the user's mailbox.

        HTTP GET gmail/v1/users/{userId}/drafts

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            maxResults (int, optional): Maximum number of drafts to return. This field defaults to 100. The maximum allowed value for this field is 500.
            pageToken (str, optional): Page token to retrieve a specific page of results in the list.
            q (str, optional): Only return draft messages matching the specified query. Supports the same query format as the Gmail search box. For example, `"from:someuser@example.com rfc822msgid: is:unread"`.
            includeSpamTrash (bool, optional): Include drafts from `SPAM` and `TRASH` in the results.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if q is not None:
            kwargs['q'] = q
        if includeSpamTrash is not None:
            kwargs['includeSpamTrash'] = includeSpamTrash

        request = self.client.users().drafts().list(**kwargs) # type: ignore
        return request.execute()

    async def users_drafts_send(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Sends the specified, existing draft to the recipients in the `To`, `Cc`, and `Bcc` headers.

        HTTP POST gmail/v1/users/{userId}/drafts/send

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().drafts().send(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().drafts().send(**kwargs) # type: ignore
        return request.execute()

    async def users_drafts_update(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Replaces a draft's content.

        HTTP PUT gmail/v1/users/{userId}/drafts/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the draft to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().drafts().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().drafts().update(**kwargs) # type: ignore
        return request.execute()

    async def users_history_list(
        self,
        userId: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        startHistoryId: Optional[str] = None,
        labelId: Optional[str] = None,
        historyTypes: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Lists the history of all changes to the given mailbox. History results are returned in chronological order (increasing `historyId`).

        HTTP GET gmail/v1/users/{userId}/history

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            maxResults (int, optional): Maximum number of history records to return. This field defaults to 100. The maximum allowed value for this field is 500.
            pageToken (str, optional): Page token to retrieve a specific page of results in the list.
            startHistoryId (str, optional): Required. Returns history records after the specified `startHistoryId`. The supplied `startHistoryId` should be obtained from the `historyId` of a message, thread, or previous `list` response. History IDs increase chronologically but are not contiguous with random gaps in between valid IDs. Supplying an invalid or out of date `startHistoryId` typically returns an `HTTP 404` error code. A `historyId` is typically valid for at least a week, but in some rare circumstances may be valid for only a few hours. If you receive an `HTTP 404` error response, your application should perform a full sync. If you receive no `nextPageToken` in the response, there are no updates to retrieve and you can store the returned `historyId` for a future request.
            labelId (str, optional): Only return messages with a label matching the ID.
            historyTypes (str, optional): History types to be returned by the function

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if startHistoryId is not None:
            kwargs['startHistoryId'] = startHistoryId
        if labelId is not None:
            kwargs['labelId'] = labelId
        if historyTypes is not None:
            kwargs['historyTypes'] = historyTypes

        request = self.client.users_history().list(**kwargs) # type: ignore
        return request.execute()

    async def users_messages_trash(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Moves the specified message to the trash.

        HTTP POST gmail/v1/users/{userId}/messages/{id}/trash

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the message to Trash.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().messages().trash(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().messages().trash(**kwargs) # type: ignore
        return request.execute()

    async def users_messages_untrash(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Removes the specified message from the trash.

        HTTP POST gmail/v1/users/{userId}/messages/{id}/untrash

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the message to remove from Trash.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().messages().untrash(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().messages().untrash(**kwargs) # type: ignore
        return request.execute()

    async def users_messages_delete(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Immediately and permanently deletes the specified message. This operation cannot be undone. Prefer `messages.trash` instead.

        HTTP DELETE gmail/v1/users/{userId}/messages/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the message to delete.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        request = self.client.users().messages().delete(**kwargs) # type: ignore
        return request.execute()

    async def users_messages_batch_delete(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Deletes many messages by message ID. Provides no guarantees that messages were not already deleted or even existed at all.

        HTTP POST gmail/v1/users/{userId}/messages/batchDelete

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().messages().batchDelete(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().messages().batchDelete(**kwargs) # type: ignore
        return request.execute()

    async def users_messages_import(
        self,
        userId: str,
        internalDateSource: Optional[str] = None,
        neverMarkSpam: Optional[bool] = None,
        processForCalendar: Optional[bool] = None,
        deleted: Optional[bool] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Imports a message into only this user's mailbox, with standard email delivery scanning and classification similar to receiving via SMTP. This method doesn't perform SPF checks, so it might not work for some spam messages, such as those attempting to perform domain spoofing. This method does not send a message.

        HTTP POST gmail/v1/users/{userId}/messages/import

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            internalDateSource (str, optional): Source for Gmail's internal date of the message.
            neverMarkSpam (bool, optional): Ignore the Gmail spam classifier decision and never mark this email as SPAM in the mailbox.
            processForCalendar (bool, optional): Process calendar invites in the email and add any extracted meetings to the Google Calendar for this user.
            deleted (bool, optional): Mark the email as permanently deleted (not TRASH) and only visible in Google Vault to a Vault administrator. Only used for Google Workspace accounts.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if internalDateSource is not None:
            kwargs['internalDateSource'] = internalDateSource
        if neverMarkSpam is not None:
            kwargs['neverMarkSpam'] = neverMarkSpam
        if processForCalendar is not None:
            kwargs['processForCalendar'] = processForCalendar
        if deleted is not None:
            kwargs['deleted'] = deleted

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = getattr(self.client.users().messages(), 'import')(**kwargs, body=body) # type: ignore
        else:
            request = getattr(self.client.users().messages(), 'import')(**kwargs) # type: ignore
        return request.execute()

    async def users_messages_insert(
        self,
        userId: str,
        internalDateSource: Optional[str] = None,
        deleted: Optional[bool] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Directly inserts a message into only this user's mailbox similar to `IMAP APPEND`, bypassing most scanning and classification. Does not send a message.

        HTTP POST gmail/v1/users/{userId}/messages

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            internalDateSource (str, optional): Source for Gmail's internal date of the message.
            deleted (bool, optional): Mark the email as permanently deleted (not TRASH) and only visible in Google Vault to a Vault administrator. Only used for Google Workspace accounts.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if internalDateSource is not None:
            kwargs['internalDateSource'] = internalDateSource
        if deleted is not None:
            kwargs['deleted'] = deleted

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().messages().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().messages().insert(**kwargs) # type: ignore
        return request.execute()

    async def users_messages_get(
        self,
        userId: str,
        id: str,
        format: Optional[str] = None,
        metadataHeaders: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets the specified message.

        HTTP GET gmail/v1/users/{userId}/messages/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the message to retrieve. This ID is usually retrieved using `messages.list`. The ID is also contained in the result when a message is inserted (`messages.insert`) or imported (`messages.import`).
            format (str, optional): The format to return the message in.
            metadataHeaders (str, optional): When given and format is `METADATA`, only include headers specified.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id
        if format is not None:
            kwargs['format'] = format
        if metadataHeaders is not None:
            kwargs['metadataHeaders'] = metadataHeaders

        request = self.client.users().messages().get(**kwargs) # type: ignore
        return request.execute()

    async def users_messages_send(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Sends the specified message to the recipients in the `To`, `Cc`, and `Bcc` headers. For example usage, see [Sending email](https://developers.google.com/workspace/gmail/api/guides/sending).

        HTTP POST gmail/v1/users/{userId}/messages/send

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().messages().send(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().messages().send(**kwargs) # type: ignore
        return request.execute()

    async def users_messages_list(
        self,
        userId: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        q: Optional[str] = None,
        labelIds: Optional[str] = None,
        includeSpamTrash: Optional[bool] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Lists the messages in the user's mailbox. For example usage, see [List Gmail messages](https://developers.google.com/workspace/gmail/api/guides/list-messages).

        HTTP GET gmail/v1/users/{userId}/messages

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            maxResults (int, optional): Maximum number of messages to return. This field defaults to 100. The maximum allowed value for this field is 500.
            pageToken (str, optional): Page token to retrieve a specific page of results in the list.
            q (str, optional): Only return messages matching the specified query. Supports the same query format as the Gmail search box. For example, `"from:someuser@example.com rfc822msgid: is:unread"`. Parameter cannot be used when accessing the api using the gmail.metadata scope.
            labelIds (str, optional): Only return messages with labels that match all of the specified label IDs. Messages in a thread might have labels that other messages in the same thread don't have. To learn more, see [Manage labels on messages and threads](https://developers.google.com/workspace/gmail/api/guides/labels#manage_labels_on_messages_threads).
            includeSpamTrash (bool, optional): Include messages from `SPAM` and `TRASH` in the results.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if q is not None:
            kwargs['q'] = q
        if labelIds is not None:
            kwargs['labelIds'] = labelIds
        if includeSpamTrash is not None:
            kwargs['includeSpamTrash'] = includeSpamTrash

        request = self.client.users().messages().list(**kwargs) # type: ignore
        return request.execute()

    async def users_messages_modify(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Modifies the labels on the specified message.

        HTTP POST gmail/v1/users/{userId}/messages/{id}/modify

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the message to modify.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().messages().modify(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().messages().modify(**kwargs) # type: ignore
        return request.execute()

    async def users_messages_batch_modify(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Modifies the labels on the specified messages.

        HTTP POST gmail/v1/users/{userId}/messages/batchModify

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().messages().batchModify(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().messages().batchModify(**kwargs) # type: ignore
        return request.execute()

    async def users_messages_attachments_get(
        self,
        userId: str,
        messageId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets the specified message attachment.

        HTTP GET gmail/v1/users/{userId}/messages/{messageId}/attachments/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            messageId (str, required): The ID of the message containing the attachment.
            id (str, required): The ID of the attachment.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if messageId is not None:
            kwargs['messageId'] = messageId
        if id is not None:
            kwargs['id'] = id

        request = self.client.users().messages().attachments().get(**kwargs) # type: ignore
        return request.execute()

    async def users_labels_create(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Creates a new label.

        HTTP POST gmail/v1/users/{userId}/labels

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().labels().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().labels().create(**kwargs) # type: ignore
        return request.execute()

    async def users_labels_delete(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Immediately and permanently deletes the specified label and removes it from any messages and threads that it is applied to.

        HTTP DELETE gmail/v1/users/{userId}/labels/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the label to delete.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        request = self.client.users().labels().delete(**kwargs) # type: ignore
        return request.execute()

    async def users_labels_get(
        self,
        userId: str,
        id: str
    ) -> Dict[str, Any]:
        """Gmail API: Gets the specified label.

        HTTP GET gmail/v1/users/{userId}/labels/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the label to retrieve.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        request = self.client.users().labels().get(**kwargs) # type: ignore
        return request.execute()

    async def users_labels_list(
        self,
        userId: str
    ) -> Dict[str, Any]:
        """Gmail API: Lists all labels in the user's mailbox.

        HTTP GET gmail/v1/users/{userId}/labels

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userId is not None:
            kwargs['userId'] = userId

        request = self.client.users().labels().list(**kwargs) # type: ignore
        return request.execute()

    async def users_labels_update(
        self,
        userId: str,
        id: str
    ) -> Dict[str, Any]:
        """Gmail API: Updates the specified label.

        HTTP PUT gmail/v1/users/{userId}/labels/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the label to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().labels().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().labels().update(**kwargs) # type: ignore
        return request.execute()

    async def users_labels_patch(
        self,
        userId: str,
        id: str
    ) -> Dict[str, Any]:
        """Gmail API: Patch the specified label.

        HTTP PATCH gmail/v1/users/{userId}/labels/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the label to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().labels().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().labels().patch(**kwargs) # type: ignore
        return request.execute()

    async def users_threads_trash(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Moves the specified thread to the trash. Any messages that belong to the thread are also moved to the trash.

        HTTP POST gmail/v1/users/{userId}/threads/{id}/trash

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the thread to Trash.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().threads().trash(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().threads().trash(**kwargs) # type: ignore
        return request.execute()

    async def users_threads_untrash(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Removes the specified thread from the trash. Any messages that belong to the thread are also removed from the trash.

        HTTP POST gmail/v1/users/{userId}/threads/{id}/untrash

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the thread to remove from Trash.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().threads().untrash(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().threads().untrash(**kwargs) # type: ignore
        return request.execute()

    async def users_threads_delete(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Immediately and permanently deletes the specified thread. Any messages that belong to the thread are also deleted. This operation cannot be undone. Prefer `threads.trash` instead.

        HTTP DELETE gmail/v1/users/{userId}/threads/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): ID of the Thread to delete.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        request = self.client.users().threads().delete(**kwargs) # type: ignore
        return request.execute()

    async def users_threads_get(
        self,
        userId: str,
        id: str,
        format: Optional[str] = None,
        metadataHeaders: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets the specified thread.

        HTTP GET gmail/v1/users/{userId}/threads/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the thread to retrieve.
            format (str, optional): The format to return the messages in.
            metadataHeaders (str, optional): When given and format is METADATA, only include headers specified.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id
        if format is not None:
            kwargs['format'] = format
        if metadataHeaders is not None:
            kwargs['metadataHeaders'] = metadataHeaders

        request = self.client.users().threads().get(**kwargs) # type: ignore
        return request.execute()

    async def users_threads_list(
        self,
        userId: str,
        maxResults: Optional[int] = None,
        pageToken: Optional[str] = None,
        q: Optional[str] = None,
        labelIds: Optional[str] = None,
        includeSpamTrash: Optional[bool] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Lists the threads in the user's mailbox.

        HTTP GET gmail/v1/users/{userId}/threads

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            maxResults (int, optional): Maximum number of threads to return. This field defaults to 100. The maximum allowed value for this field is 500.
            pageToken (str, optional): Page token to retrieve a specific page of results in the list.
            q (str, optional): Only return threads matching the specified query. Supports the same query format as the Gmail search box. For example, `"from:someuser@example.com rfc822msgid: is:unread"`. Parameter cannot be used when accessing the api using the gmail.metadata scope.
            labelIds (str, optional): Only return threads with labels that match all of the specified label IDs.
            includeSpamTrash (bool, optional): Include threads from `SPAM` and `TRASH` in the results.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if maxResults is not None:
            kwargs['maxResults'] = maxResults
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if q is not None:
            kwargs['q'] = q
        if labelIds is not None:
            kwargs['labelIds'] = labelIds
        if includeSpamTrash is not None:
            kwargs['includeSpamTrash'] = includeSpamTrash

        request = self.client.users().threads().list(**kwargs) # type: ignore
        return request.execute()

    async def users_threads_modify(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Modifies the labels applied to the thread. This applies to all messages in the thread.

        HTTP POST gmail/v1/users/{userId}/threads/{id}/modify

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            id (str, required): The ID of the thread to modify.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().threads().modify(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().threads().modify(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_get_imap(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets IMAP settings.

        HTTP GET gmail/v1/users/{userId}/settings/imap

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        request = self.client.users().settings().getImap(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_update_imap(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Updates IMAP settings.

        HTTP PUT gmail/v1/users/{userId}/settings/imap

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().settings().updateImap(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().settings().updateImap(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_get_pop(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets POP settings.

        HTTP GET gmail/v1/users/{userId}/settings/pop

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        request = self.client.users().settings().getPop(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_update_pop(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Updates POP settings.

        HTTP PUT gmail/v1/users/{userId}/settings/pop

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().settings().updatePop(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().settings().updatePop(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_get_vacation(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets vacation responder settings.

        HTTP GET gmail/v1/users/{userId}/settings/vacation

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        request = self.client.users().settings().getVacation(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_update_vacation(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Updates vacation responder settings.

        HTTP PUT gmail/v1/users/{userId}/settings/vacation

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().settings().updateVacation(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().settings().updateVacation(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_get_language(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets language settings.

        HTTP GET gmail/v1/users/{userId}/settings/language

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        request = self.client.users().settings().getLanguage(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_update_language(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Updates language settings. If successful, the return object contains the `displayLanguage` that was saved for the user, which may differ from the value passed into the request. This is because the requested `displayLanguage` may not be directly supported by Gmail but have a close variant that is, and so the variant may be chosen and saved instead.

        HTTP PUT gmail/v1/users/{userId}/settings/language

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().settings().updateLanguage(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().settings().updateLanguage(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_get_auto_forwarding(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets the auto-forwarding setting for the specified account.

        HTTP GET gmail/v1/users/{userId}/settings/autoForwarding

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        request = self.client.users().settings().getAutoForwarding(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_update_auto_forwarding(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Updates the auto-forwarding setting for the specified account. A verified forwarding address must be specified when auto-forwarding is enabled. This method is only available to service account clients that have been delegated domain-wide authority.

        HTTP PUT gmail/v1/users/{userId}/settings/autoForwarding

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users().settings().updateAutoForwarding(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users().settings().updateAutoForwarding(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_send_as_list(
        self,
        userId: str
    ) -> Dict[str, Any]:
        """Gmail API: Lists the send-as aliases for the specified account. The result includes the primary send-as address associated with the account as well as any custom "from" aliases.

        HTTP GET gmail/v1/users/{userId}/settings/sendAs

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userId is not None:
            kwargs['userId'] = userId

        request = self.client.users_settings_sendAs().list(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_send_as_get(
        self,
        userId: str,
        sendAsEmail: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets the specified send-as alias. Fails with an HTTP 404 error if the specified address is not a member of the collection.

        HTTP GET gmail/v1/users/{userId}/settings/sendAs/{sendAsEmail}

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.
            sendAsEmail (str, required): The send-as alias to be retrieved.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if sendAsEmail is not None:
            kwargs['sendAsEmail'] = sendAsEmail

        request = self.client.users_settings_sendAs().get(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_send_as_create(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Creates a custom "from" send-as alias. If an SMTP MSA is specified, Gmail will attempt to connect to the SMTP service to validate the configuration before creating the alias. If ownership verification is required for the alias, a message will be sent to the email address and the resource's verification status will be set to `pending`; otherwise, the resource will be created with verification status set to `accepted`. If a signature is provided, Gmail will sanitize the HTML before saving it with the alias. This method is only available to service account clients that have been delegated domain-wide authority.

        HTTP POST gmail/v1/users/{userId}/settings/sendAs

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_sendAs().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_sendAs().create(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_send_as_update(
        self,
        userId: str,
        sendAsEmail: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Updates a send-as alias. If a signature is provided, Gmail will sanitize the HTML before saving it with the alias. Addresses other than the primary address for the account can only be updated by service account clients that have been delegated domain-wide authority.

        HTTP PUT gmail/v1/users/{userId}/settings/sendAs/{sendAsEmail}

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.
            sendAsEmail (str, required): The send-as alias to be updated.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if sendAsEmail is not None:
            kwargs['sendAsEmail'] = sendAsEmail

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_sendAs().update(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_sendAs().update(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_send_as_patch(
        self,
        userId: str,
        sendAsEmail: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Patch the specified send-as alias.

        HTTP PATCH gmail/v1/users/{userId}/settings/sendAs/{sendAsEmail}

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.
            sendAsEmail (str, required): The send-as alias to be updated.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if sendAsEmail is not None:
            kwargs['sendAsEmail'] = sendAsEmail

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_sendAs().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_sendAs().patch(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_send_as_delete(
        self,
        userId: str,
        sendAsEmail: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Deletes the specified send-as alias. Revokes any verification that may have been required for using it. This method is only available to service account clients that have been delegated domain-wide authority.

        HTTP DELETE gmail/v1/users/{userId}/settings/sendAs/{sendAsEmail}

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.
            sendAsEmail (str, required): The send-as alias to be deleted.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if sendAsEmail is not None:
            kwargs['sendAsEmail'] = sendAsEmail

        request = self.client.users_settings_sendAs().delete(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_send_as_verify(
        self,
        userId: str,
        sendAsEmail: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Sends a verification email to the specified send-as alias address. The verification status must be `pending`. This method is only available to service account clients that have been delegated domain-wide authority.

        HTTP POST gmail/v1/users/{userId}/settings/sendAs/{sendAsEmail}/verify

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.
            sendAsEmail (str, required): The send-as alias to be verified.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if userId is not None:
            kwargs['userId'] = userId
        if sendAsEmail is not None:
            kwargs['sendAsEmail'] = sendAsEmail

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_sendAs().verify(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_sendAs().verify(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_send_as_smime_info_list(
        self,
        userId: str,
        sendAsEmail: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Lists S/MIME configs for the specified send-as alias.

        HTTP GET gmail/v1/users/{userId}/settings/sendAs/{sendAsEmail}/smimeInfo

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            sendAsEmail (str, required): The email address that appears in the "From:" header for mail sent using this alias.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if sendAsEmail is not None:
            kwargs['sendAsEmail'] = sendAsEmail

        request = self.client.users_settings_sendAs_smimeInfo().list(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_send_as_smime_info_get(
        self,
        userId: str,
        sendAsEmail: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets the specified S/MIME config for the specified send-as alias.

        HTTP GET gmail/v1/users/{userId}/settings/sendAs/{sendAsEmail}/smimeInfo/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            sendAsEmail (str, required): The email address that appears in the "From:" header for mail sent using this alias.
            id (str, required): The immutable ID for the SmimeInfo.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if sendAsEmail is not None:
            kwargs['sendAsEmail'] = sendAsEmail
        if id is not None:
            kwargs['id'] = id

        request = self.client.users_settings_sendAs_smimeInfo().get(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_send_as_smime_info_insert(
        self,
        userId: str,
        sendAsEmail: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Insert (upload) the given S/MIME config for the specified send-as alias. Note that pkcs12 format is required for the key.

        HTTP POST gmail/v1/users/{userId}/settings/sendAs/{sendAsEmail}/smimeInfo

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            sendAsEmail (str, required): The email address that appears in the "From:" header for mail sent using this alias.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if sendAsEmail is not None:
            kwargs['sendAsEmail'] = sendAsEmail

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_sendAs_smimeInfo().insert(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_sendAs_smimeInfo().insert(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_send_as_smime_info_delete(
        self,
        userId: str,
        sendAsEmail: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Deletes the specified S/MIME config for the specified send-as alias.

        HTTP DELETE gmail/v1/users/{userId}/settings/sendAs/{sendAsEmail}/smimeInfo/{id}

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            sendAsEmail (str, required): The email address that appears in the "From:" header for mail sent using this alias.
            id (str, required): The immutable ID for the SmimeInfo.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if sendAsEmail is not None:
            kwargs['sendAsEmail'] = sendAsEmail
        if id is not None:
            kwargs['id'] = id

        request = self.client.users_settings_sendAs_smimeInfo().delete(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_send_as_smime_info_set_default(
        self,
        userId: str,
        sendAsEmail: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Sets the default S/MIME config for the specified send-as alias.

        HTTP POST gmail/v1/users/{userId}/settings/sendAs/{sendAsEmail}/smimeInfo/{id}/setDefault

        Args:
            userId (str, required): The user's email address. The special value `me` can be used to indicate the authenticated user.
            sendAsEmail (str, required): The email address that appears in the "From:" header for mail sent using this alias.
            id (str, required): The immutable ID for the SmimeInfo.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if sendAsEmail is not None:
            kwargs['sendAsEmail'] = sendAsEmail
        if id is not None:
            kwargs['id'] = id

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_sendAs_smimeInfo().setDefault(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_sendAs_smimeInfo().setDefault(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_cse_identities_create(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Creates and configures a client-side encryption identity that's authorized to send mail from the user account. Google publishes the S/MIME certificate to a shared domain-wide directory so that people within a Google Workspace organization can encrypt and send mail to the identity. For administrators managing identities and keypairs for users in their organization, requests require authorization with a [service account](https://developers.google.com/identity/protocols/OAuth2ServiceAccount) that has [domain-wide delegation authority](https://developers.google.com/identity/protocols/OAuth2ServiceAccount#delegatingauthority) to impersonate users with the `https://www.googleapis.com/auth/gmail.settings.basic` scope. For users managing their own identities and keypairs, requests require [hardware key encryption](https://support.google.com/a/answer/14153163) turned on and configured.

        HTTP POST gmail/v1/users/{userId}/settings/cse/identities

        Args:
            userId (str, required): The requester's primary email address. To indicate the authenticated user, you can use the special value `me`.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_cse_identities().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_cse_identities().create(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_cse_identities_delete(
        self,
        userId: str,
        cseEmailAddress: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Deletes a client-side encryption identity. The authenticated user can no longer use the identity to send encrypted messages. You cannot restore the identity after you delete it. Instead, use the CreateCseIdentity method to create another identity with the same configuration. For administrators managing identities and keypairs for users in their organization, requests require authorization with a [service account](https://developers.google.com/identity/protocols/OAuth2ServiceAccount) that has [domain-wide delegation authority](https://developers.google.com/identity/protocols/OAuth2ServiceAccount#delegatingauthority) to impersonate users with the `https://www.googleapis.com/auth/gmail.settings.basic` scope. For users managing their own identities and keypairs, requests require [hardware key encryption](https://support.google.com/a/answer/14153163) turned on and configured.

        HTTP DELETE gmail/v1/users/{userId}/settings/cse/identities/{cseEmailAddress}

        Args:
            userId (str, required): The requester's primary email address. To indicate the authenticated user, you can use the special value `me`.
            cseEmailAddress (str, required): The primary email address associated with the client-side encryption identity configuration that's removed.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if cseEmailAddress is not None:
            kwargs['cseEmailAddress'] = cseEmailAddress

        request = self.client.users_settings_cse_identities().delete(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_cse_identities_get(
        self,
        userId: str,
        cseEmailAddress: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Retrieves a client-side encryption identity configuration. For administrators managing identities and keypairs for users in their organization, requests require authorization with a [service account](https://developers.google.com/identity/protocols/OAuth2ServiceAccount) that has [domain-wide delegation authority](https://developers.google.com/identity/protocols/OAuth2ServiceAccount#delegatingauthority) to impersonate users with the `https://www.googleapis.com/auth/gmail.settings.basic` scope. For users managing their own identities and keypairs, requests require [hardware key encryption](https://support.google.com/a/answer/14153163) turned on and configured.

        HTTP GET gmail/v1/users/{userId}/settings/cse/identities/{cseEmailAddress}

        Args:
            userId (str, required): The requester's primary email address. To indicate the authenticated user, you can use the special value `me`.
            cseEmailAddress (str, required): The primary email address associated with the client-side encryption identity configuration that's retrieved.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if cseEmailAddress is not None:
            kwargs['cseEmailAddress'] = cseEmailAddress

        request = self.client.users_settings_cse_identities().get(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_cse_identities_list(
        self,
        userId: str,
        pageToken: Optional[str] = None,
        pageSize: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Lists the client-side encrypted identities for an authenticated user. For administrators managing identities and keypairs for users in their organization, requests require authorization with a [service account](https://developers.google.com/identity/protocols/OAuth2ServiceAccount) that has [domain-wide delegation authority](https://developers.google.com/identity/protocols/OAuth2ServiceAccount#delegatingauthority) to impersonate users with the `https://www.googleapis.com/auth/gmail.settings.basic` scope. For users managing their own identities and keypairs, requests require [hardware key encryption](https://support.google.com/a/answer/14153163) turned on and configured.

        HTTP GET gmail/v1/users/{userId}/settings/cse/identities

        Args:
            userId (str, required): The requester's primary email address. To indicate the authenticated user, you can use the special value `me`.
            pageToken (str, optional): Pagination token indicating which page of identities to return. If the token is not supplied, then the API will return the first page of results.
            pageSize (int, optional): The number of identities to return. If not provided, the page size will default to 20 entries.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if pageSize is not None:
            kwargs['pageSize'] = pageSize

        request = self.client.users_settings_cse_identities().list(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_cse_identities_patch(
        self,
        userId: str,
        emailAddress: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Associates a different key pair with an existing client-side encryption identity. The updated key pair must validate against Google's [S/MIME certificate profiles](https://support.google.com/a/answer/7300887). For administrators managing identities and keypairs for users in their organization, requests require authorization with a [service account](https://developers.google.com/identity/protocols/OAuth2ServiceAccount) that has [domain-wide delegation authority](https://developers.google.com/identity/protocols/OAuth2ServiceAccount#delegatingauthority) to impersonate users with the `https://www.googleapis.com/auth/gmail.settings.basic` scope. For users managing their own identities and keypairs, requests require [hardware key encryption](https://support.google.com/a/answer/14153163) turned on and configured.

        HTTP PATCH gmail/v1/users/{userId}/settings/cse/identities/{emailAddress}

        Args:
            userId (str, required): The requester's primary email address. To indicate the authenticated user, you can use the special value `me`.
            emailAddress (str, required): The email address of the client-side encryption identity to update.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if emailAddress is not None:
            kwargs['emailAddress'] = emailAddress

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_cse_identities().patch(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_cse_identities().patch(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_cse_keypairs_create(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Creates and uploads a client-side encryption S/MIME public key certificate chain and private key metadata for the authenticated user. For administrators managing identities and keypairs for users in their organization, requests require authorization with a [service account](https://developers.google.com/identity/protocols/OAuth2ServiceAccount) that has [domain-wide delegation authority](https://developers.google.com/identity/protocols/OAuth2ServiceAccount#delegatingauthority) to impersonate users with the `https://www.googleapis.com/auth/gmail.settings.basic` scope. For users managing their own identities and keypairs, requests require [hardware key encryption](https://support.google.com/a/answer/14153163) turned on and configured.

        HTTP POST gmail/v1/users/{userId}/settings/cse/keypairs

        Args:
            userId (str, required): The requester's primary email address. To indicate the authenticated user, you can use the special value `me`.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_cse_keypairs().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_cse_keypairs().create(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_cse_keypairs_disable(
        self,
        userId: str,
        keyPairId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Turns off a client-side encryption key pair. The authenticated user can no longer use the key pair to decrypt incoming CSE message texts or sign outgoing CSE mail. To regain access, use the EnableCseKeyPair to turn on the key pair. After 30 days, you can permanently delete the key pair by using the ObliterateCseKeyPair method. For administrators managing identities and keypairs for users in their organization, requests require authorization with a [service account](https://developers.google.com/identity/protocols/OAuth2ServiceAccount) that has [domain-wide delegation authority](https://developers.google.com/identity/protocols/OAuth2ServiceAccount#delegatingauthority) to impersonate users with the `https://www.googleapis.com/auth/gmail.settings.basic` scope. For users managing their own identities and keypairs, requests require [hardware key encryption](https://support.google.com/a/answer/14153163) turned on and configured.

        HTTP POST gmail/v1/users/{userId}/settings/cse/keypairs/{keyPairId}:disable

        Args:
            userId (str, required): The requester's primary email address. To indicate the authenticated user, you can use the special value `me`.
            keyPairId (str, required): The identifier of the key pair to turn off.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if keyPairId is not None:
            kwargs['keyPairId'] = keyPairId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_cse_keypairs().disable(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_cse_keypairs().disable(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_cse_keypairs_enable(
        self,
        userId: str,
        keyPairId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Turns on a client-side encryption key pair that was turned off. The key pair becomes active again for any associated client-side encryption identities. For administrators managing identities and keypairs for users in their organization, requests require authorization with a [service account](https://developers.google.com/identity/protocols/OAuth2ServiceAccount) that has [domain-wide delegation authority](https://developers.google.com/identity/protocols/OAuth2ServiceAccount#delegatingauthority) to impersonate users with the `https://www.googleapis.com/auth/gmail.settings.basic` scope. For users managing their own identities and keypairs, requests require [hardware key encryption](https://support.google.com/a/answer/14153163) turned on and configured.

        HTTP POST gmail/v1/users/{userId}/settings/cse/keypairs/{keyPairId}:enable

        Args:
            userId (str, required): The requester's primary email address. To indicate the authenticated user, you can use the special value `me`.
            keyPairId (str, required): The identifier of the key pair to turn on.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if keyPairId is not None:
            kwargs['keyPairId'] = keyPairId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_cse_keypairs().enable(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_cse_keypairs().enable(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_cse_keypairs_get(
        self,
        userId: str,
        keyPairId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Retrieves an existing client-side encryption key pair. For administrators managing identities and keypairs for users in their organization, requests require authorization with a [service account](https://developers.google.com/identity/protocols/OAuth2ServiceAccount) that has [domain-wide delegation authority](https://developers.google.com/identity/protocols/OAuth2ServiceAccount#delegatingauthority) to impersonate users with the `https://www.googleapis.com/auth/gmail.settings.basic` scope. For users managing their own identities and keypairs, requests require [hardware key encryption](https://support.google.com/a/answer/14153163) turned on and configured.

        HTTP GET gmail/v1/users/{userId}/settings/cse/keypairs/{keyPairId}

        Args:
            userId (str, required): The requester's primary email address. To indicate the authenticated user, you can use the special value `me`.
            keyPairId (str, required): The identifier of the key pair to retrieve.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if keyPairId is not None:
            kwargs['keyPairId'] = keyPairId

        request = self.client.users_settings_cse_keypairs().get(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_cse_keypairs_list(
        self,
        userId: str,
        pageToken: Optional[str] = None,
        pageSize: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Lists client-side encryption key pairs for an authenticated user. For administrators managing identities and keypairs for users in their organization, requests require authorization with a [service account](https://developers.google.com/identity/protocols/OAuth2ServiceAccount) that has [domain-wide delegation authority](https://developers.google.com/identity/protocols/OAuth2ServiceAccount#delegatingauthority) to impersonate users with the `https://www.googleapis.com/auth/gmail.settings.basic` scope. For users managing their own identities and keypairs, requests require [hardware key encryption](https://support.google.com/a/answer/14153163) turned on and configured.

        HTTP GET gmail/v1/users/{userId}/settings/cse/keypairs

        Args:
            userId (str, required): The requester's primary email address. To indicate the authenticated user, you can use the special value `me`.
            pageToken (str, optional): Pagination token indicating which page of key pairs to return. If the token is not supplied, then the API will return the first page of results.
            pageSize (int, optional): The number of key pairs to return. If not provided, the page size will default to 20 entries.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if pageToken is not None:
            kwargs['pageToken'] = pageToken
        if pageSize is not None:
            kwargs['pageSize'] = pageSize

        request = self.client.users_settings_cse_keypairs().list(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_cse_keypairs_obliterate(
        self,
        userId: str,
        keyPairId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Deletes a client-side encryption key pair permanently and immediately. You can only permanently delete key pairs that have been turned off for more than 30 days. To turn off a key pair, use the DisableCseKeyPair method. Gmail can't restore or decrypt any messages that were encrypted by an obliterated key. Authenticated users and Google Workspace administrators lose access to reading the encrypted messages. For administrators managing identities and keypairs for users in their organization, requests require authorization with a [service account](https://developers.google.com/identity/protocols/OAuth2ServiceAccount) that has [domain-wide delegation authority](https://developers.google.com/identity/protocols/OAuth2ServiceAccount#delegatingauthority) to impersonate users with the `https://www.googleapis.com/auth/gmail.settings.basic` scope. For users managing their own identities and keypairs, requests require [hardware key encryption](https://support.google.com/a/answer/14153163) turned on and configured.

        HTTP POST gmail/v1/users/{userId}/settings/cse/keypairs/{keyPairId}:obliterate

        Args:
            userId (str, required): The requester's primary email address. To indicate the authenticated user, you can use the special value `me`.
            keyPairId (str, required): The identifier of the key pair to obliterate.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if keyPairId is not None:
            kwargs['keyPairId'] = keyPairId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_cse_keypairs().obliterate(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_cse_keypairs().obliterate(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_filters_list(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Lists the message filters of a Gmail user.

        HTTP GET gmail/v1/users/{userId}/settings/filters

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        request = self.client.users_settings_filters().list(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_filters_get(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets a filter.

        HTTP GET gmail/v1/users/{userId}/settings/filters/{id}

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.
            id (str, required): The ID of the filter to be fetched.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        request = self.client.users_settings_filters().get(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_filters_create(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Creates a filter. Note: you can only create a maximum of 1,000 filters.

        HTTP POST gmail/v1/users/{userId}/settings/filters

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_filters().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_filters().create(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_filters_delete(
        self,
        userId: str,
        id: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Immediately and permanently deletes the specified filter.

        HTTP DELETE gmail/v1/users/{userId}/settings/filters/{id}

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.
            id (str, required): The ID of the filter to be deleted.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if id is not None:
            kwargs['id'] = id

        request = self.client.users_settings_filters().delete(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_forwarding_addresses_list(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Lists the forwarding addresses for the specified account.

        HTTP GET gmail/v1/users/{userId}/settings/forwardingAddresses

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        request = self.client.users_settings_forwardingAddresses().list(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_forwarding_addresses_get(
        self,
        userId: str,
        forwardingEmail: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets the specified forwarding address.

        HTTP GET gmail/v1/users/{userId}/settings/forwardingAddresses/{forwardingEmail}

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.
            forwardingEmail (str, required): The forwarding address to be retrieved.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if forwardingEmail is not None:
            kwargs['forwardingEmail'] = forwardingEmail

        request = self.client.users_settings_forwardingAddresses().get(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_forwarding_addresses_create(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Creates a forwarding address. If ownership verification is required, a message will be sent to the recipient and the resource's verification status will be set to `pending`; otherwise, the resource will be created with verification status set to `accepted`. This method is only available to service account clients that have been delegated domain-wide authority.

        HTTP POST gmail/v1/users/{userId}/settings/forwardingAddresses

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_forwardingAddresses().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_forwardingAddresses().create(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_forwarding_addresses_delete(
        self,
        userId: str,
        forwardingEmail: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Deletes the specified forwarding address and revokes any verification that may have been required. This method is only available to service account clients that have been delegated domain-wide authority.

        HTTP DELETE gmail/v1/users/{userId}/settings/forwardingAddresses/{forwardingEmail}

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.
            forwardingEmail (str, required): The forwarding address to be deleted.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if forwardingEmail is not None:
            kwargs['forwardingEmail'] = forwardingEmail

        request = self.client.users_settings_forwardingAddresses().delete(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_delegates_list(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Lists the delegates for the specified account. This method is only available to service account clients that have been delegated domain-wide authority.

        HTTP GET gmail/v1/users/{userId}/settings/delegates

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        request = self.client.users_settings_delegates().list(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_delegates_get(
        self,
        userId: str,
        delegateEmail: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Gets the specified delegate. Note that a delegate user must be referred to by their primary email address, and not an email alias. This method is only available to service account clients that have been delegated domain-wide authority.

        HTTP GET gmail/v1/users/{userId}/settings/delegates/{delegateEmail}

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.
            delegateEmail (str, required): The email address of the user whose delegate relationship is to be retrieved.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if delegateEmail is not None:
            kwargs['delegateEmail'] = delegateEmail

        request = self.client.users_settings_delegates().get(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_delegates_create(
        self,
        userId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Adds a delegate with its verification status set directly to `accepted`, without sending any verification email. The delegate user must be a member of the same Google Workspace organization as the delegator user. Gmail imposes limitations on the number of delegates and delegators each user in a Google Workspace organization can have. These limits depend on your organization, but in general each user can have up to 25 delegates and up to 10 delegators. Note that a delegate user must be referred to by their primary email address, and not an email alias. Also note that when a new delegate is created, there may be up to a one minute delay before the new delegate is available for use. This method is only available to service account clients that have been delegated domain-wide authority.

        HTTP POST gmail/v1/users/{userId}/settings/delegates

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.users_settings_delegates().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.users_settings_delegates().create(**kwargs) # type: ignore
        return request.execute()

    async def users_settings_delegates_delete(
        self,
        userId: str,
        delegateEmail: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Gmail API: Removes the specified delegate (which can be of any verification status), and revokes any verification that may have been required for using it. Note that a delegate user must be referred to by their primary email address, and not an email alias. This method is only available to service account clients that have been delegated domain-wide authority.

        HTTP DELETE gmail/v1/users/{userId}/settings/delegates/{delegateEmail}

        Args:
            userId (str, required): User's email address. The special value "me" can be used to indicate the authenticated user.
            delegateEmail (str, required): The email address of the user to be removed as a delegate.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if userId is not None:
            kwargs['userId'] = userId
        if delegateEmail is not None:
            kwargs['delegateEmail'] = delegateEmail

        request = self.client.users_settings_delegates().delete(**kwargs) # type: ignore
        return request.execute()

    async def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
