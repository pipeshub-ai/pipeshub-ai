from typing import Any, Dict, Optional

from app.sources.client.google.google import GoogleClient


class GoogleFormsDataSource:
    """
    Auto-generated Google Forms API client wrapper.
    Uses Google SDK client internally for all operations.
    This class wraps all Google Forms API v1 methods and provides
    a consistent interface while using the official Google SDK.
    """
    def __init__(
        self,
        client: GoogleClient
    ) -> None:
        """
        Initialize with Google Forms API client.
        Args:
            client: Google Forms API client from build('forms', 'v1', credentials=credentials)
        """
        self.client = client

    async def forms_create(
        self,
        unpublished: Optional[bool] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Forms API: Create a new form using the title given in the provided form message in the request. *Important:* Only the form.info.title and form.info.document_title fields are copied to the new form. All other fields including the form description, items and settings are disallowed. To create a new form and add items, you must first call forms.create to create an empty form with a title and (optional) document title, and then call forms.update to add the items.

        HTTP POST v1/forms

        Args:
            unpublished (bool, optional): Optional. Whether the form is unpublished. If set to `true`, the form doesn't accept responses. If set to `false` or unset, the form is published and accepts responses.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if unpublished is not None:
            kwargs['unpublished'] = unpublished

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.forms().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.forms().create(**kwargs) # type: ignore
        return request.execute()

    async def forms_get(
        self,
        formId: str
    ) -> Dict[str, Any]:
        """Google Forms API: Get a form.

        HTTP GET v1/forms/{formId}

        Args:
            formId (str, required): Required. The form ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = {}
        if formId is not None:
            kwargs['formId'] = formId

        request = self.client.forms().get(**kwargs) # type: ignore
        return request.execute()

    async def forms_batch_update(
        self,
        formId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Forms API: Change the form with a batch of updates.

        HTTP POST v1/forms/{formId}:batchUpdate

        Args:
            formId (str, required): Required. The form ID.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if formId is not None:
            kwargs['formId'] = formId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.forms().batchUpdate(**kwargs, body=body) # type: ignore
        else:
            request = self.client.forms().batchUpdate(**kwargs) # type: ignore
        return request.execute()

    async def forms_set_publish_settings(
        self,
        formId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Forms API: Updates the publish settings of a form. Legacy forms aren't supported because they don't have the `publish_settings` field.

        HTTP POST v1/forms/{formId}:setPublishSettings

        Args:
            formId (str, required): Required. The ID of the form. You can get the id from Form.form_id field.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if formId is not None:
            kwargs['formId'] = formId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.forms().setPublishSettings(**kwargs, body=body) # type: ignore
        else:
            request = self.client.forms().setPublishSettings(**kwargs) # type: ignore
        return request.execute()

    async def forms_responses_get(
        self,
        formId: str,
        responseId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Forms API: Get one response from the form.

        HTTP GET v1/forms/{formId}/responses/{responseId}

        Args:
            formId (str, required): Required. The form ID.
            responseId (str, required): Required. The response ID within the form.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if formId is not None:
            kwargs['formId'] = formId
        if responseId is not None:
            kwargs['responseId'] = responseId

        request = self.client.forms_responses().get(**kwargs) # type: ignore
        return request.execute()

    async def forms_responses_list(
        self,
        formId: str,
        filter: Optional[str] = None,
        pageSize: Optional[int] = None,
        pageToken: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Forms API: List a form's responses.

        HTTP GET v1/forms/{formId}/responses

        Args:
            formId (str, required): Required. ID of the Form whose responses to list.
            filter (str, optional): Which form responses to return. Currently, the only supported filters are: * timestamp > *N* which means to get all form responses submitted after (but not at) timestamp *N*. * timestamp >= *N* which means to get all form responses submitted at and after timestamp *N*. For both supported filters, timestamp must be formatted in RFC3339 UTC "Zulu" format. Examples: "2014-10-02T15:01:23Z" and "2014-10-02T15:01:23.045123456Z".
            pageSize (int, optional): The maximum number of responses to return. The service may return fewer than this value. If unspecified or zero, at most 5000 responses are returned.
            pageToken (str, optional): A page token returned by a previous list response. If this field is set, the form and the values of the filter must be the same as for the original request.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if formId is not None:
            kwargs['formId'] = formId
        if filter is not None:
            kwargs['filter'] = filter
        if pageSize is not None:
            kwargs['pageSize'] = pageSize
        if pageToken is not None:
            kwargs['pageToken'] = pageToken

        request = self.client.forms_responses().list(**kwargs) # type: ignore
        return request.execute()

    async def forms_watches_create(
        self,
        formId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Forms API: Create a new watch. If a watch ID is provided, it must be unused. For each invoking project, the per form limit is one watch per Watch.EventType. A watch expires seven days after it is created (see Watch.expire_time).

        HTTP POST v1/forms/{formId}/watches

        Args:
            formId (str, required): Required. ID of the Form to watch.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if formId is not None:
            kwargs['formId'] = formId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.forms_watches().create(**kwargs, body=body) # type: ignore
        else:
            request = self.client.forms_watches().create(**kwargs) # type: ignore
        return request.execute()

    async def forms_watches_list(
        self,
        formId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Forms API: Return a list of the watches owned by the invoking project. The maximum number of watches is two: For each invoker, the limit is one for each event type per form.

        HTTP GET v1/forms/{formId}/watches

        Args:
            formId (str, required): Required. ID of the Form whose watches to list.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if formId is not None:
            kwargs['formId'] = formId

        request = self.client.forms_watches().list(**kwargs) # type: ignore
        return request.execute()

    async def forms_watches_renew(
        self,
        formId: str,
        watchId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Forms API: Renew an existing watch for seven days. The state of the watch after renewal is `ACTIVE`, and the `expire_time` is seven days from the renewal. Renewing a watch in an error state (e.g. `SUSPENDED`) succeeds if the error is no longer present, but fail otherwise. After a watch has expired, RenewWatch returns `NOT_FOUND`.

        HTTP POST v1/forms/{formId}/watches/{watchId}:renew

        Args:
            formId (str, required): Required. The ID of the Form.
            watchId (str, required): Required. The ID of the Watch to renew.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if formId is not None:
            kwargs['formId'] = formId
        if watchId is not None:
            kwargs['watchId'] = watchId

        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = self.client.forms_watches().renew(**kwargs, body=body) # type: ignore
        else:
            request = self.client.forms_watches().renew(**kwargs) # type: ignore
        return request.execute()

    async def forms_watches_delete(
        self,
        formId: str,
        watchId: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Google Forms API: Delete a watch.

        HTTP DELETE v1/forms/{formId}/watches/{watchId}

        Args:
            formId (str, required): Required. The ID of the Form.
            watchId (str, required): Required. The ID of the Watch to delete.

        Returns:
            Dict[str, Any]: API response
        """
        kwargs = kwargs or {}
        if formId is not None:
            kwargs['formId'] = formId
        if watchId is not None:
            kwargs['watchId'] = watchId

        request = self.client.forms_watches().delete(**kwargs) # type: ignore
        return request.execute()

    async def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
