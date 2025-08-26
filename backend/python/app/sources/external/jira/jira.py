from typing import Any, Dict, Optional, Union

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.jira.jira import JiraClient


class JiraDataSource:
    def __init__(self, client: JiraClient) -> None:
        """Default init for the connector-specific data source."""
        self._client = client.get_client()
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        try:
            self.base_url = self._client.get_base_url().rstrip('/') # type: ignore [valid method]
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'JiraDataSource':
        return self

    async def get_banner(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get announcement banner configuration\n\nHTTP GET /rest/api/3/announcementBanner"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/announcementBanner'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_banner(
        self,
        isDismissible: Optional[bool] = None,
        isEnabled: Optional[bool] = None,
        message: Optional[str] = None,
        visibility: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update announcement banner configuration\n\nHTTP PUT /rest/api/3/announcementBanner\nBody (application/json) fields:\n  - isDismissible (bool, optional)\n  - isEnabled (bool, optional)\n  - message (str, optional)\n  - visibility (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if isDismissible is not None:
            _body['isDismissible'] = isDismissible
        if isEnabled is not None:
            _body['isEnabled'] = isEnabled
        if message is not None:
            _body['message'] = message
        if visibility is not None:
            _body['visibility'] = visibility
        rel_path = '/rest/api/3/announcementBanner'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_fields_configurations(
        self,
        fieldIdsOrKeys: list[str],
        id: Optional[list[int]] = None,
        fieldContextId: Optional[list[int]] = None,
        issueId: Optional[int] = None,
        projectKeyOrId: Optional[str] = None,
        issueTypeId: Optional[str] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk get custom field configurations\n\nHTTP POST /rest/api/3/app/field/context/configuration/list\nQuery params:\n  - id (list[int], optional)\n  - fieldContextId (list[int], optional)\n  - issueId (int, optional)\n  - projectKeyOrId (str, optional)\n  - issueTypeId (str, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)\nBody (application/json) fields:\n  - fieldIdsOrKeys (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if id is not None:
            _query['id'] = id
        if fieldContextId is not None:
            _query['fieldContextId'] = fieldContextId
        if issueId is not None:
            _query['issueId'] = issueId
        if projectKeyOrId is not None:
            _query['projectKeyOrId'] = projectKeyOrId
        if issueTypeId is not None:
            _query['issueTypeId'] = issueTypeId
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body: Dict[str, Any] = {}
        _body['fieldIdsOrKeys'] = fieldIdsOrKeys
        rel_path = '/rest/api/3/app/field/context/configuration/list'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_multiple_custom_field_values(
        self,
        generateChangelog: Optional[bool] = None,
        updates: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update custom fields\n\nHTTP POST /rest/api/3/app/field/value\nQuery params:\n  - generateChangelog (bool, optional)\nBody (application/json) fields:\n  - updates (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if generateChangelog is not None:
            _query['generateChangelog'] = generateChangelog
        _body: Dict[str, Any] = {}
        if updates is not None:
            _body['updates'] = updates
        rel_path = '/rest/api/3/app/field/value'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_field_configuration(
        self,
        fieldIdOrKey: str,
        id: Optional[list[int]] = None,
        fieldContextId: Optional[list[int]] = None,
        issueId: Optional[int] = None,
        projectKeyOrId: Optional[str] = None,
        issueTypeId: Optional[str] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom field configurations\n\nHTTP GET /rest/api/3/app/field/{fieldIdOrKey}/context/configuration\nPath params:\n  - fieldIdOrKey (str)\nQuery params:\n  - id (list[int], optional)\n  - fieldContextId (list[int], optional)\n  - issueId (int, optional)\n  - projectKeyOrId (str, optional)\n  - issueTypeId (str, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldIdOrKey': fieldIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if id is not None:
            _query['id'] = id
        if fieldContextId is not None:
            _query['fieldContextId'] = fieldContextId
        if issueId is not None:
            _query['issueId'] = issueId
        if projectKeyOrId is not None:
            _query['projectKeyOrId'] = projectKeyOrId
        if issueTypeId is not None:
            _query['issueTypeId'] = issueTypeId
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/app/field/{fieldIdOrKey}/context/configuration'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_custom_field_configuration(
        self,
        fieldIdOrKey: str,
        configurations: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update custom field configurations\n\nHTTP PUT /rest/api/3/app/field/{fieldIdOrKey}/context/configuration\nPath params:\n  - fieldIdOrKey (str)\nBody (application/json) fields:\n  - configurations (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldIdOrKey': fieldIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['configurations'] = configurations
        rel_path = '/rest/api/3/app/field/{fieldIdOrKey}/context/configuration'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_custom_field_value(
        self,
        fieldIdOrKey: str,
        generateChangelog: Optional[bool] = None,
        updates: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update custom field value\n\nHTTP PUT /rest/api/3/app/field/{fieldIdOrKey}/value\nPath params:\n  - fieldIdOrKey (str)\nQuery params:\n  - generateChangelog (bool, optional)\nBody (application/json) fields:\n  - updates (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldIdOrKey': fieldIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if generateChangelog is not None:
            _query['generateChangelog'] = generateChangelog
        _body: Dict[str, Any] = {}
        if updates is not None:
            _body['updates'] = updates
        rel_path = '/rest/api/3/app/field/{fieldIdOrKey}/value'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_application_property(
        self,
        key: Optional[str] = None,
        permissionLevel: Optional[str] = None,
        keyFilter: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get application property\n\nHTTP GET /rest/api/3/application-properties\nQuery params:\n  - key (str, optional)\n  - permissionLevel (str, optional)\n  - keyFilter (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        if permissionLevel is not None:
            _query['permissionLevel'] = permissionLevel
        if keyFilter is not None:
            _query['keyFilter'] = keyFilter
        _body = None
        rel_path = '/rest/api/3/application-properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_advanced_settings(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get advanced settings\n\nHTTP GET /rest/api/3/application-properties/advanced-settings"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/application-properties/advanced-settings'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_application_property(
        self,
        id: str,
        id_body: Optional[str] = None,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set application property\n\nHTTP PUT /rest/api/3/application-properties/{id}\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - id (str, optional)\n  - value (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if id_body is not None:
            _body['id'] = id_body
        if value is not None:
            _body['value'] = value
        rel_path = '/rest/api/3/application-properties/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_application_roles(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all application roles\n\nHTTP GET /rest/api/3/applicationrole"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/applicationrole'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_application_role(
        self,
        key: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get application role\n\nHTTP GET /rest/api/3/applicationrole/{key}\nPath params:\n  - key (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'key': key,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/applicationrole/{key}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_attachment_content(
        self,
        id: str,
        redirect: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get attachment content\n\nHTTP GET /rest/api/3/attachment/content/{id}\nPath params:\n  - id (str)\nQuery params:\n  - redirect (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if redirect is not None:
            _query['redirect'] = redirect
        _body = None
        rel_path = '/rest/api/3/attachment/content/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_attachment_meta(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Jira attachment settings\n\nHTTP GET /rest/api/3/attachment/meta"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/attachment/meta'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_attachment_thumbnail(
        self,
        id: str,
        redirect: Optional[bool] = None,
        fallbackToDefault: Optional[bool] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get attachment thumbnail\n\nHTTP GET /rest/api/3/attachment/thumbnail/{id}\nPath params:\n  - id (str)\nQuery params:\n  - redirect (bool, optional)\n  - fallbackToDefault (bool, optional)\n  - width (int, optional)\n  - height (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if redirect is not None:
            _query['redirect'] = redirect
        if fallbackToDefault is not None:
            _query['fallbackToDefault'] = fallbackToDefault
        if width is not None:
            _query['width'] = width
        if height is not None:
            _query['height'] = height
        _body = None
        rel_path = '/rest/api/3/attachment/thumbnail/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_attachment(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete attachment\n\nHTTP DELETE /rest/api/3/attachment/{id}\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/attachment/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_attachment(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get attachment metadata\n\nHTTP GET /rest/api/3/attachment/{id}\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/attachment/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def expand_attachment_for_humans(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all metadata for an expanded attachment\n\nHTTP GET /rest/api/3/attachment/{id}/expand/human\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/attachment/{id}/expand/human'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def expand_attachment_for_machines(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get contents metadata for an expanded attachment\n\nHTTP GET /rest/api/3/attachment/{id}/expand/raw\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/attachment/{id}/expand/raw'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_audit_records(
        self,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        filter: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get audit records\n\nHTTP GET /rest/api/3/auditing/record\nQuery params:\n  - offset (int, optional)\n  - limit (int, optional)\n  - filter (str, optional)\n  - from (str, optional)\n  - to (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if offset is not None:
            _query['offset'] = offset
        if limit is not None:
            _query['limit'] = limit
        if filter is not None:
            _query['filter'] = filter
        if from_ is not None:
            _query['from'] = from_
        if to is not None:
            _query['to'] = to
        _body = None
        rel_path = '/rest/api/3/auditing/record'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_system_avatars(
        self,
        type: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get system avatars by type\n\nHTTP GET /rest/api/3/avatar/{type}/system\nPath params:\n  - type (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'type': type,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/avatar/{type}/system'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def submit_bulk_delete(
        self,
        selectedIssueIdsOrKeys: list[str],
        sendBulkNotification: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk delete issues\n\nHTTP POST /rest/api/3/bulk/issues/delete\nBody (application/json) fields:\n  - selectedIssueIdsOrKeys (list[str], required)\n  - sendBulkNotification (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['selectedIssueIdsOrKeys'] = selectedIssueIdsOrKeys
        if sendBulkNotification is not None:
            _body['sendBulkNotification'] = sendBulkNotification
        rel_path = '/rest/api/3/bulk/issues/delete'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_bulk_editable_fields(
        self,
        issueIdsOrKeys: str,
        searchText: Optional[str] = None,
        endingBefore: Optional[str] = None,
        startingAfter: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get bulk editable fields\n\nHTTP GET /rest/api/3/bulk/issues/fields\nQuery params:\n  - issueIdsOrKeys (str, required)\n  - searchText (str, optional)\n  - endingBefore (str, optional)\n  - startingAfter (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['issueIdsOrKeys'] = issueIdsOrKeys
        if searchText is not None:
            _query['searchText'] = searchText
        if endingBefore is not None:
            _query['endingBefore'] = endingBefore
        if startingAfter is not None:
            _query['startingAfter'] = startingAfter
        _body = None
        rel_path = '/rest/api/3/bulk/issues/fields'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def submit_bulk_edit(
        self,
        editedFieldsInput: Dict[str, Any],
        selectedActions: list[str],
        selectedIssueIdsOrKeys: list[str],
        sendBulkNotification: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk edit issues\n\nHTTP POST /rest/api/3/bulk/issues/fields\nBody (application/json) fields:\n  - editedFieldsInput (Dict[str, Any], required)\n  - selectedActions (list[str], required)\n  - selectedIssueIdsOrKeys (list[str], required)\n  - sendBulkNotification (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['editedFieldsInput'] = editedFieldsInput
        _body['selectedActions'] = selectedActions
        _body['selectedIssueIdsOrKeys'] = selectedIssueIdsOrKeys
        if sendBulkNotification is not None:
            _body['sendBulkNotification'] = sendBulkNotification
        rel_path = '/rest/api/3/bulk/issues/fields'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def submit_bulk_move(
        self,
        sendBulkNotification: Optional[bool] = None,
        targetToSourcesMapping: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk move issues\n\nHTTP POST /rest/api/3/bulk/issues/move\nBody (application/json) fields:\n  - sendBulkNotification (bool, optional)\n  - targetToSourcesMapping (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if sendBulkNotification is not None:
            _body['sendBulkNotification'] = sendBulkNotification
        if targetToSourcesMapping is not None:
            _body['targetToSourcesMapping'] = targetToSourcesMapping
        rel_path = '/rest/api/3/bulk/issues/move'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_available_transitions(
        self,
        issueIdsOrKeys: str,
        endingBefore: Optional[str] = None,
        startingAfter: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get available transitions\n\nHTTP GET /rest/api/3/bulk/issues/transition\nQuery params:\n  - issueIdsOrKeys (str, required)\n  - endingBefore (str, optional)\n  - startingAfter (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['issueIdsOrKeys'] = issueIdsOrKeys
        if endingBefore is not None:
            _query['endingBefore'] = endingBefore
        if startingAfter is not None:
            _query['startingAfter'] = startingAfter
        _body = None
        rel_path = '/rest/api/3/bulk/issues/transition'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def submit_bulk_transition(
        self,
        bulkTransitionInputs: list[Dict[str, Any]],
        sendBulkNotification: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk transition issue statuses\n\nHTTP POST /rest/api/3/bulk/issues/transition\nBody (application/json) fields:\n  - bulkTransitionInputs (list[Dict[str, Any]], required)\n  - sendBulkNotification (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['bulkTransitionInputs'] = bulkTransitionInputs
        if sendBulkNotification is not None:
            _body['sendBulkNotification'] = sendBulkNotification
        rel_path = '/rest/api/3/bulk/issues/transition'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def submit_bulk_unwatch(
        self,
        selectedIssueIdsOrKeys: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk unwatch issues\n\nHTTP POST /rest/api/3/bulk/issues/unwatch\nBody (application/json) fields:\n  - selectedIssueIdsOrKeys (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['selectedIssueIdsOrKeys'] = selectedIssueIdsOrKeys
        rel_path = '/rest/api/3/bulk/issues/unwatch'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def submit_bulk_watch(
        self,
        selectedIssueIdsOrKeys: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk watch issues\n\nHTTP POST /rest/api/3/bulk/issues/watch\nBody (application/json) fields:\n  - selectedIssueIdsOrKeys (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['selectedIssueIdsOrKeys'] = selectedIssueIdsOrKeys
        rel_path = '/rest/api/3/bulk/issues/watch'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_bulk_operation_progress(
        self,
        taskId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get bulk issue operation progress\n\nHTTP GET /rest/api/3/bulk/queue/{taskId}\nPath params:\n  - taskId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'taskId': taskId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/bulk/queue/{taskId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_bulk_changelogs(
        self,
        issueIdsOrKeys: list[str],
        fieldIds: Optional[list[str]] = None,
        maxResults: Optional[int] = None,
        nextPageToken: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk fetch changelogs\n\nHTTP POST /rest/api/3/changelog/bulkfetch\nBody (application/json) fields:\n  - fieldIds (list[str], optional)\n  - issueIdsOrKeys (list[str], required)\n  - maxResults (int, optional)\n  - nextPageToken (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if fieldIds is not None:
            _body['fieldIds'] = fieldIds
        _body['issueIdsOrKeys'] = issueIdsOrKeys
        if maxResults is not None:
            _body['maxResults'] = maxResults
        if nextPageToken is not None:
            _body['nextPageToken'] = nextPageToken
        rel_path = '/rest/api/3/changelog/bulkfetch'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_user_data_classification_levels(
        self,
        status: Optional[list[str]] = None,
        orderBy: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all classification levels\n\nHTTP GET /rest/api/3/classification-levels\nQuery params:\n  - status (list[str], optional)\n  - orderBy (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if status is not None:
            _query['status'] = status
        if orderBy is not None:
            _query['orderBy'] = orderBy
        _body = None
        rel_path = '/rest/api/3/classification-levels'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_comments_by_ids(
        self,
        ids: list[int],
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get comments by IDs\n\nHTTP POST /rest/api/3/comment/list\nQuery params:\n  - expand (str, optional)\nBody (application/json) fields:\n  - ids (list[int], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body: Dict[str, Any] = {}
        _body['ids'] = ids
        rel_path = '/rest/api/3/comment/list'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_comment_property_keys(
        self,
        commentId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get comment property keys\n\nHTTP GET /rest/api/3/comment/{commentId}/properties\nPath params:\n  - commentId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'commentId': commentId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/comment/{commentId}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_comment_property(
        self,
        commentId: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete comment property\n\nHTTP DELETE /rest/api/3/comment/{commentId}/properties/{propertyKey}\nPath params:\n  - commentId (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'commentId': commentId,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/comment/{commentId}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_comment_property(
        self,
        commentId: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get comment property\n\nHTTP GET /rest/api/3/comment/{commentId}/properties/{propertyKey}\nPath params:\n  - commentId (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'commentId': commentId,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/comment/{commentId}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_comment_property(
        self,
        commentId: str,
        propertyKey: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set comment property\n\nHTTP PUT /rest/api/3/comment/{commentId}/properties/{propertyKey}\nPath params:\n  - commentId (str)\n  - propertyKey (str)\nBody: application/json (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'commentId': commentId,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/api/3/comment/{commentId}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def find_components_for_projects(
        self,
        projectIdsOrKeys: Optional[list[str]] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        query: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Find components for projects\n\nHTTP GET /rest/api/3/component\nQuery params:\n  - projectIdsOrKeys (list[str], optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - orderBy (str, optional)\n  - query (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if projectIdsOrKeys is not None:
            _query['projectIdsOrKeys'] = projectIdsOrKeys
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if query is not None:
            _query['query'] = query
        _body = None
        rel_path = '/rest/api/3/component'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_component(
        self,
        ari: Optional[str] = None,
        assignee: Optional[Dict[str, Any]] = None,
        assigneeType: Optional[str] = None,
        description: Optional[str] = None,
        id: Optional[str] = None,
        isAssigneeTypeValid: Optional[bool] = None,
        lead: Optional[Dict[str, Any]] = None,
        leadAccountId: Optional[str] = None,
        leadUserName: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        project: Optional[str] = None,
        projectId: Optional[int] = None,
        realAssignee: Optional[Dict[str, Any]] = None,
        realAssigneeType: Optional[str] = None,
        self_: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create component\n\nHTTP POST /rest/api/3/component\nBody (application/json) fields:\n  - ari (str, optional)\n  - assignee (Dict[str, Any], optional)\n  - assigneeType (str, optional)\n  - description (str, optional)\n  - id (str, optional)\n  - isAssigneeTypeValid (bool, optional)\n  - lead (Dict[str, Any], optional)\n  - leadAccountId (str, optional)\n  - leadUserName (str, optional)\n  - metadata (Dict[str, Any], optional)\n  - name (str, optional)\n  - project (str, optional)\n  - projectId (int, optional)\n  - realAssignee (Dict[str, Any], optional)\n  - realAssigneeType (str, optional)\n  - self (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if ari is not None:
            _body['ari'] = ari
        if assignee is not None:
            _body['assignee'] = assignee
        if assigneeType is not None:
            _body['assigneeType'] = assigneeType
        if description is not None:
            _body['description'] = description
        if id is not None:
            _body['id'] = id
        if isAssigneeTypeValid is not None:
            _body['isAssigneeTypeValid'] = isAssigneeTypeValid
        if lead is not None:
            _body['lead'] = lead
        if leadAccountId is not None:
            _body['leadAccountId'] = leadAccountId
        if leadUserName is not None:
            _body['leadUserName'] = leadUserName
        if metadata is not None:
            _body['metadata'] = metadata
        if name is not None:
            _body['name'] = name
        if project is not None:
            _body['project'] = project
        if projectId is not None:
            _body['projectId'] = projectId
        if realAssignee is not None:
            _body['realAssignee'] = realAssignee
        if realAssigneeType is not None:
            _body['realAssigneeType'] = realAssigneeType
        if self_ is not None:
            _body['self'] = self_
        rel_path = '/rest/api/3/component'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_component(
        self,
        id: str,
        moveIssuesTo: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete component\n\nHTTP DELETE /rest/api/3/component/{id}\nPath params:\n  - id (str)\nQuery params:\n  - moveIssuesTo (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if moveIssuesTo is not None:
            _query['moveIssuesTo'] = moveIssuesTo
        _body = None
        rel_path = '/rest/api/3/component/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_component(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get component\n\nHTTP GET /rest/api/3/component/{id}\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/component/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_component(
        self,
        id: str,
        ari: Optional[str] = None,
        assignee: Optional[Dict[str, Any]] = None,
        assigneeType: Optional[str] = None,
        description: Optional[str] = None,
        id_body: Optional[str] = None,
        isAssigneeTypeValid: Optional[bool] = None,
        lead: Optional[Dict[str, Any]] = None,
        leadAccountId: Optional[str] = None,
        leadUserName: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        project: Optional[str] = None,
        projectId: Optional[int] = None,
        realAssignee: Optional[Dict[str, Any]] = None,
        realAssigneeType: Optional[str] = None,
        self_: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update component\n\nHTTP PUT /rest/api/3/component/{id}\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - ari (str, optional)\n  - assignee (Dict[str, Any], optional)\n  - assigneeType (str, optional)\n  - description (str, optional)\n  - id (str, optional)\n  - isAssigneeTypeValid (bool, optional)\n  - lead (Dict[str, Any], optional)\n  - leadAccountId (str, optional)\n  - leadUserName (str, optional)\n  - metadata (Dict[str, Any], optional)\n  - name (str, optional)\n  - project (str, optional)\n  - projectId (int, optional)\n  - realAssignee (Dict[str, Any], optional)\n  - realAssigneeType (str, optional)\n  - self (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if ari is not None:
            _body['ari'] = ari
        if assignee is not None:
            _body['assignee'] = assignee
        if assigneeType is not None:
            _body['assigneeType'] = assigneeType
        if description is not None:
            _body['description'] = description
        if id_body is not None:
            _body['id'] = id_body
        if isAssigneeTypeValid is not None:
            _body['isAssigneeTypeValid'] = isAssigneeTypeValid
        if lead is not None:
            _body['lead'] = lead
        if leadAccountId is not None:
            _body['leadAccountId'] = leadAccountId
        if leadUserName is not None:
            _body['leadUserName'] = leadUserName
        if metadata is not None:
            _body['metadata'] = metadata
        if name is not None:
            _body['name'] = name
        if project is not None:
            _body['project'] = project
        if projectId is not None:
            _body['projectId'] = projectId
        if realAssignee is not None:
            _body['realAssignee'] = realAssignee
        if realAssigneeType is not None:
            _body['realAssigneeType'] = realAssigneeType
        if self_ is not None:
            _body['self'] = self_
        rel_path = '/rest/api/3/component/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_component_related_issues(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get component issues count\n\nHTTP GET /rest/api/3/component/{id}/relatedIssueCounts\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/component/{id}/relatedIssueCounts'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_configuration(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get global settings\n\nHTTP GET /rest/api/3/configuration"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/configuration'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_selected_time_tracking_implementation(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get selected time tracking provider\n\nHTTP GET /rest/api/3/configuration/timetracking"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/configuration/timetracking'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def select_time_tracking_implementation(
        self,
        key: str,
        name: Optional[str] = None,
        url: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Select time tracking provider\n\nHTTP PUT /rest/api/3/configuration/timetracking\nBody (application/json) fields:\n  - key (str, required)\n  - name (str, optional)\n  - url (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['key'] = key
        if name is not None:
            _body['name'] = name
        if url is not None:
            _body['url'] = url
        rel_path = '/rest/api/3/configuration/timetracking'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_available_time_tracking_implementations(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all time tracking providers\n\nHTTP GET /rest/api/3/configuration/timetracking/list"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/configuration/timetracking/list'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_shared_time_tracking_configuration(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get time tracking settings\n\nHTTP GET /rest/api/3/configuration/timetracking/options"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/configuration/timetracking/options'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_shared_time_tracking_configuration(
        self,
        defaultUnit: str,
        timeFormat: str,
        workingDaysPerWeek: float,
        workingHoursPerDay: float,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set time tracking settings\n\nHTTP PUT /rest/api/3/configuration/timetracking/options\nBody (application/json) fields:\n  - defaultUnit (str, required)\n  - timeFormat (str, required)\n  - workingDaysPerWeek (float, required)\n  - workingHoursPerDay (float, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['defaultUnit'] = defaultUnit
        _body['timeFormat'] = timeFormat
        _body['workingDaysPerWeek'] = workingDaysPerWeek
        _body['workingHoursPerDay'] = workingHoursPerDay
        rel_path = '/rest/api/3/configuration/timetracking/options'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_field_option(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom field option\n\nHTTP GET /rest/api/3/customFieldOption/{id}\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/customFieldOption/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_dashboards(
        self,
        filter: Optional[str] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all dashboards\n\nHTTP GET /rest/api/3/dashboard\nQuery params:\n  - filter (str, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if filter is not None:
            _query['filter'] = filter
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/dashboard'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_dashboard(
        self,
        editPermissions: list[Dict[str, Any]],
        name: str,
        sharePermissions: list[Dict[str, Any]],
        extendAdminPermissions: Optional[bool] = None,
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create dashboard\n\nHTTP POST /rest/api/3/dashboard\nQuery params:\n  - extendAdminPermissions (bool, optional)\nBody (application/json) fields:\n  - description (str, optional)\n  - editPermissions (list[Dict[str, Any]], required)\n  - name (str, required)\n  - sharePermissions (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if extendAdminPermissions is not None:
            _query['extendAdminPermissions'] = extendAdminPermissions
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['editPermissions'] = editPermissions
        _body['name'] = name
        _body['sharePermissions'] = sharePermissions
        rel_path = '/rest/api/3/dashboard'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def bulk_edit_dashboards(
        self,
        action: str,
        entityIds: list[int],
        changeOwnerDetails: Optional[Dict[str, Any]] = None,
        extendAdminPermissions: Optional[bool] = None,
        permissionDetails: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk edit dashboards\n\nHTTP PUT /rest/api/3/dashboard/bulk/edit\nBody (application/json) fields:\n  - action (str, required)\n  - changeOwnerDetails (Dict[str, Any], optional)\n  - entityIds (list[int], required)\n  - extendAdminPermissions (bool, optional)\n  - permissionDetails (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['action'] = action
        if changeOwnerDetails is not None:
            _body['changeOwnerDetails'] = changeOwnerDetails
        _body['entityIds'] = entityIds
        if extendAdminPermissions is not None:
            _body['extendAdminPermissions'] = extendAdminPermissions
        if permissionDetails is not None:
            _body['permissionDetails'] = permissionDetails
        rel_path = '/rest/api/3/dashboard/bulk/edit'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_available_dashboard_gadgets(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get available gadgets\n\nHTTP GET /rest/api/3/dashboard/gadgets"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/dashboard/gadgets'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_dashboards_paginated(
        self,
        dashboardName: Optional[str] = None,
        accountId: Optional[str] = None,
        owner: Optional[str] = None,
        groupname: Optional[str] = None,
        groupId: Optional[str] = None,
        projectId: Optional[int] = None,
        orderBy: Optional[str] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        status: Optional[str] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Search for dashboards\n\nHTTP GET /rest/api/3/dashboard/search\nQuery params:\n  - dashboardName (str, optional)\n  - accountId (str, optional)\n  - owner (str, optional)\n  - groupname (str, optional)\n  - groupId (str, optional)\n  - projectId (int, optional)\n  - orderBy (str, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - status (str, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if dashboardName is not None:
            _query['dashboardName'] = dashboardName
        if accountId is not None:
            _query['accountId'] = accountId
        if owner is not None:
            _query['owner'] = owner
        if groupname is not None:
            _query['groupname'] = groupname
        if groupId is not None:
            _query['groupId'] = groupId
        if projectId is not None:
            _query['projectId'] = projectId
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if status is not None:
            _query['status'] = status
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/dashboard/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_gadgets(
        self,
        dashboardId: int,
        moduleKey: Optional[list[str]] = None,
        uri: Optional[list[str]] = None,
        gadgetId: Optional[list[int]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get gadgets\n\nHTTP GET /rest/api/3/dashboard/{dashboardId}/gadget\nPath params:\n  - dashboardId (int)\nQuery params:\n  - moduleKey (list[str], optional)\n  - uri (list[str], optional)\n  - gadgetId (list[int], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'dashboardId': dashboardId,
        }
        _query: Dict[str, Any] = {}
        if moduleKey is not None:
            _query['moduleKey'] = moduleKey
        if uri is not None:
            _query['uri'] = uri
        if gadgetId is not None:
            _query['gadgetId'] = gadgetId
        _body = None
        rel_path = '/rest/api/3/dashboard/{dashboardId}/gadget'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_gadget(
        self,
        dashboardId: int,
        color: Optional[str] = None,
        ignoreUriAndModuleKeyValidation: Optional[bool] = None,
        moduleKey: Optional[str] = None,
        position: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
        uri: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add gadget to dashboard\n\nHTTP POST /rest/api/3/dashboard/{dashboardId}/gadget\nPath params:\n  - dashboardId (int)\nBody (application/json) fields:\n  - color (str, optional)\n  - ignoreUriAndModuleKeyValidation (bool, optional)\n  - moduleKey (str, optional)\n  - position (Dict[str, Any], optional)\n  - title (str, optional)\n  - uri (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'dashboardId': dashboardId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if color is not None:
            _body['color'] = color
        if ignoreUriAndModuleKeyValidation is not None:
            _body['ignoreUriAndModuleKeyValidation'] = ignoreUriAndModuleKeyValidation
        if moduleKey is not None:
            _body['moduleKey'] = moduleKey
        if position is not None:
            _body['position'] = position
        if title is not None:
            _body['title'] = title
        if uri is not None:
            _body['uri'] = uri
        rel_path = '/rest/api/3/dashboard/{dashboardId}/gadget'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_gadget(
        self,
        dashboardId: int,
        gadgetId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove gadget from dashboard\n\nHTTP DELETE /rest/api/3/dashboard/{dashboardId}/gadget/{gadgetId}\nPath params:\n  - dashboardId (int)\n  - gadgetId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'dashboardId': dashboardId,
            'gadgetId': gadgetId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/dashboard/{dashboardId}/gadget/{gadgetId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_gadget(
        self,
        dashboardId: int,
        gadgetId: int,
        color: Optional[str] = None,
        position: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update gadget on dashboard\n\nHTTP PUT /rest/api/3/dashboard/{dashboardId}/gadget/{gadgetId}\nPath params:\n  - dashboardId (int)\n  - gadgetId (int)\nBody (application/json) fields:\n  - color (str, optional)\n  - position (Dict[str, Any], optional)\n  - title (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'dashboardId': dashboardId,
            'gadgetId': gadgetId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if color is not None:
            _body['color'] = color
        if position is not None:
            _body['position'] = position
        if title is not None:
            _body['title'] = title
        rel_path = '/rest/api/3/dashboard/{dashboardId}/gadget/{gadgetId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_dashboard_item_property_keys(
        self,
        dashboardId: str,
        itemId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get dashboard item property keys\n\nHTTP GET /rest/api/3/dashboard/{dashboardId}/items/{itemId}/properties\nPath params:\n  - dashboardId (str)\n  - itemId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'dashboardId': dashboardId,
            'itemId': itemId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/dashboard/{dashboardId}/items/{itemId}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_dashboard_item_property(
        self,
        dashboardId: str,
        itemId: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete dashboard item property\n\nHTTP DELETE /rest/api/3/dashboard/{dashboardId}/items/{itemId}/properties/{propertyKey}\nPath params:\n  - dashboardId (str)\n  - itemId (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'dashboardId': dashboardId,
            'itemId': itemId,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/dashboard/{dashboardId}/items/{itemId}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_dashboard_item_property(
        self,
        dashboardId: str,
        itemId: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get dashboard item property\n\nHTTP GET /rest/api/3/dashboard/{dashboardId}/items/{itemId}/properties/{propertyKey}\nPath params:\n  - dashboardId (str)\n  - itemId (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'dashboardId': dashboardId,
            'itemId': itemId,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/dashboard/{dashboardId}/items/{itemId}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_dashboard_item_property(
        self,
        dashboardId: str,
        itemId: str,
        propertyKey: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set dashboard item property\n\nHTTP PUT /rest/api/3/dashboard/{dashboardId}/items/{itemId}/properties/{propertyKey}\nPath params:\n  - dashboardId (str)\n  - itemId (str)\n  - propertyKey (str)\nBody: application/json (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'dashboardId': dashboardId,
            'itemId': itemId,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/api/3/dashboard/{dashboardId}/items/{itemId}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_dashboard(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete dashboard\n\nHTTP DELETE /rest/api/3/dashboard/{id}\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/dashboard/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_dashboard(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get dashboard\n\nHTTP GET /rest/api/3/dashboard/{id}\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/dashboard/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_dashboard(
        self,
        id: str,
        editPermissions: list[Dict[str, Any]],
        name: str,
        sharePermissions: list[Dict[str, Any]],
        extendAdminPermissions: Optional[bool] = None,
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update dashboard\n\nHTTP PUT /rest/api/3/dashboard/{id}\nPath params:\n  - id (str)\nQuery params:\n  - extendAdminPermissions (bool, optional)\nBody (application/json) fields:\n  - description (str, optional)\n  - editPermissions (list[Dict[str, Any]], required)\n  - name (str, required)\n  - sharePermissions (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if extendAdminPermissions is not None:
            _query['extendAdminPermissions'] = extendAdminPermissions
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['editPermissions'] = editPermissions
        _body['name'] = name
        _body['sharePermissions'] = sharePermissions
        rel_path = '/rest/api/3/dashboard/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def copy_dashboard(
        self,
        id: str,
        editPermissions: list[Dict[str, Any]],
        name: str,
        sharePermissions: list[Dict[str, Any]],
        extendAdminPermissions: Optional[bool] = None,
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Copy dashboard\n\nHTTP POST /rest/api/3/dashboard/{id}/copy\nPath params:\n  - id (str)\nQuery params:\n  - extendAdminPermissions (bool, optional)\nBody (application/json) fields:\n  - description (str, optional)\n  - editPermissions (list[Dict[str, Any]], required)\n  - name (str, required)\n  - sharePermissions (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if extendAdminPermissions is not None:
            _query['extendAdminPermissions'] = extendAdminPermissions
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['editPermissions'] = editPermissions
        _body['name'] = name
        _body['sharePermissions'] = sharePermissions
        rel_path = '/rest/api/3/dashboard/{id}/copy'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_policy(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get data policy for the workspace\n\nHTTP GET /rest/api/3/data-policy"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/data-policy'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_policies(
        self,
        ids: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get data policy for projects\n\nHTTP GET /rest/api/3/data-policy/project\nQuery params:\n  - ids (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if ids is not None:
            _query['ids'] = ids
        _body = None
        rel_path = '/rest/api/3/data-policy/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_events(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get events\n\nHTTP GET /rest/api/3/events"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/events'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def analyse_expression(
        self,
        expressions: list[str],
        check: Optional[str] = None,
        contextVariables: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Analyse Jira expression\n\nHTTP POST /rest/api/3/expression/analyse\nQuery params:\n  - check (str, optional)\nBody (application/json) fields:\n  - contextVariables (Dict[str, Any], optional)\n  - expressions (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if check is not None:
            _query['check'] = check
        _body: Dict[str, Any] = {}
        if contextVariables is not None:
            _body['contextVariables'] = contextVariables
        _body['expressions'] = expressions
        rel_path = '/rest/api/3/expression/analyse'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def evaluate_jira_expression(
        self,
        expression: str,
        expand: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Currently being removed. Evaluate Jira expression\n\nHTTP POST /rest/api/3/expression/eval\nQuery params:\n  - expand (str, optional)\nBody (application/json) fields:\n  - context (Dict[str, Any], optional)\n  - expression (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body: Dict[str, Any] = {}
        if context is not None:
            _body['context'] = context
        _body['expression'] = expression
        rel_path = '/rest/api/3/expression/eval'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def evaluate_jsis_jira_expression(
        self,
        expression: str,
        expand: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Evaluate Jira expression using enhanced search API\n\nHTTP POST /rest/api/3/expression/evaluate\nQuery params:\n  - expand (str, optional)\nBody (application/json) fields:\n  - context (Dict[str, Any], optional)\n  - expression (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body: Dict[str, Any] = {}
        if context is not None:
            _body['context'] = context
        _body['expression'] = expression
        rel_path = '/rest/api/3/expression/evaluate'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_fields(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get fields\n\nHTTP GET /rest/api/3/field"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/field'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_custom_field(
        self,
        name: str,
        type: str,
        description: Optional[str] = None,
        searcherKey: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create custom field\n\nHTTP POST /rest/api/3/field\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, required)\n  - searcherKey (str, optional)\n  - type (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['name'] = name
        if searcherKey is not None:
            _body['searcherKey'] = searcherKey
        _body['type'] = type
        rel_path = '/rest/api/3/field'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_associations(
        self,
        associationContexts: list[Dict[str, Any]],
        fields: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove associations\n\nHTTP DELETE /rest/api/3/field/association\nBody (application/json) fields:\n  - associationContexts (list[Dict[str, Any]], required)\n  - fields (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['associationContexts'] = associationContexts
        _body['fields'] = fields
        rel_path = '/rest/api/3/field/association'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_associations(
        self,
        associationContexts: list[Dict[str, Any]],
        fields: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create associations\n\nHTTP PUT /rest/api/3/field/association\nBody (application/json) fields:\n  - associationContexts (list[Dict[str, Any]], required)\n  - fields (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['associationContexts'] = associationContexts
        _body['fields'] = fields
        rel_path = '/rest/api/3/field/association'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_fields_paginated(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        type: Optional[list[str]] = None,
        id: Optional[list[str]] = None,
        query: Optional[str] = None,
        orderBy: Optional[str] = None,
        expand: Optional[str] = None,
        projectIds: Optional[list[int]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get fields paginated\n\nHTTP GET /rest/api/3/field/search\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - type (list[str], optional)\n  - id (list[str], optional)\n  - query (str, optional)\n  - orderBy (str, optional)\n  - expand (str, optional)\n  - projectIds (list[int], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if type is not None:
            _query['type'] = type
        if id is not None:
            _query['id'] = id
        if query is not None:
            _query['query'] = query
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if expand is not None:
            _query['expand'] = expand
        if projectIds is not None:
            _query['projectIds'] = projectIds
        _body = None
        rel_path = '/rest/api/3/field/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_trashed_fields_paginated(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        id: Optional[list[str]] = None,
        query: Optional[str] = None,
        expand: Optional[str] = None,
        orderBy: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get fields in trash paginated\n\nHTTP GET /rest/api/3/field/search/trashed\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - id (list[str], optional)\n  - query (str, optional)\n  - expand (str, optional)\n  - orderBy (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        if query is not None:
            _query['query'] = query
        if expand is not None:
            _query['expand'] = expand
        if orderBy is not None:
            _query['orderBy'] = orderBy
        _body = None
        rel_path = '/rest/api/3/field/search/trashed'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_custom_field(
        self,
        fieldId: str,
        description: Optional[str] = None,
        name: Optional[str] = None,
        searcherKey: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update custom field\n\nHTTP PUT /rest/api/3/field/{fieldId}\nPath params:\n  - fieldId (str)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, optional)\n  - searcherKey (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        if searcherKey is not None:
            _body['searcherKey'] = searcherKey
        rel_path = '/rest/api/3/field/{fieldId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_contexts_for_field(
        self,
        fieldId: str,
        isAnyIssueType: Optional[bool] = None,
        isGlobalContext: Optional[bool] = None,
        contextId: Optional[list[int]] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom field contexts\n\nHTTP GET /rest/api/3/field/{fieldId}/context\nPath params:\n  - fieldId (str)\nQuery params:\n  - isAnyIssueType (bool, optional)\n  - isGlobalContext (bool, optional)\n  - contextId (list[int], optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
        }
        _query: Dict[str, Any] = {}
        if isAnyIssueType is not None:
            _query['isAnyIssueType'] = isAnyIssueType
        if isGlobalContext is not None:
            _query['isGlobalContext'] = isGlobalContext
        if contextId is not None:
            _query['contextId'] = contextId
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/field/{fieldId}/context'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_custom_field_context(
        self,
        fieldId: str,
        name: str,
        description: Optional[str] = None,
        id: Optional[str] = None,
        issueTypeIds: Optional[list[str]] = None,
        projectIds: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create custom field context\n\nHTTP POST /rest/api/3/field/{fieldId}/context\nPath params:\n  - fieldId (str)\nBody (application/json) fields:\n  - description (str, optional)\n  - id (str, optional)\n  - issueTypeIds (list[str], optional)\n  - name (str, required)\n  - projectIds (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if id is not None:
            _body['id'] = id
        if issueTypeIds is not None:
            _body['issueTypeIds'] = issueTypeIds
        _body['name'] = name
        if projectIds is not None:
            _body['projectIds'] = projectIds
        rel_path = '/rest/api/3/field/{fieldId}/context'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_default_values(
        self,
        fieldId: str,
        contextId: Optional[list[int]] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom field contexts default values\n\nHTTP GET /rest/api/3/field/{fieldId}/context/defaultValue\nPath params:\n  - fieldId (str)\nQuery params:\n  - contextId (list[int], optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
        }
        _query: Dict[str, Any] = {}
        if contextId is not None:
            _query['contextId'] = contextId
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/field/{fieldId}/context/defaultValue'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_default_values(
        self,
        fieldId: str,
        defaultValues: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set custom field contexts default values\n\nHTTP PUT /rest/api/3/field/{fieldId}/context/defaultValue\nPath params:\n  - fieldId (str)\nBody (application/json) fields:\n  - defaultValues (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if defaultValues is not None:
            _body['defaultValues'] = defaultValues
        rel_path = '/rest/api/3/field/{fieldId}/context/defaultValue'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_type_mappings_for_contexts(
        self,
        fieldId: str,
        contextId: Optional[list[int]] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue types for custom field context\n\nHTTP GET /rest/api/3/field/{fieldId}/context/issuetypemapping\nPath params:\n  - fieldId (str)\nQuery params:\n  - contextId (list[int], optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
        }
        _query: Dict[str, Any] = {}
        if contextId is not None:
            _query['contextId'] = contextId
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/field/{fieldId}/context/issuetypemapping'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_field_contexts_for_projects_and_issue_types(
        self,
        fieldId: str,
        mappings: list[Dict[str, Any]],
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom field contexts for projects and issue types\n\nHTTP POST /rest/api/3/field/{fieldId}/context/mapping\nPath params:\n  - fieldId (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\nBody (application/json) fields:\n  - mappings (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body: Dict[str, Any] = {}
        _body['mappings'] = mappings
        rel_path = '/rest/api/3/field/{fieldId}/context/mapping'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_context_mapping(
        self,
        fieldId: str,
        contextId: Optional[list[int]] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project mappings for custom field context\n\nHTTP GET /rest/api/3/field/{fieldId}/context/projectmapping\nPath params:\n  - fieldId (str)\nQuery params:\n  - contextId (list[int], optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
        }
        _query: Dict[str, Any] = {}
        if contextId is not None:
            _query['contextId'] = contextId
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/field/{fieldId}/context/projectmapping'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_custom_field_context(
        self,
        fieldId: str,
        contextId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete custom field context\n\nHTTP DELETE /rest/api/3/field/{fieldId}/context/{contextId}\nPath params:\n  - fieldId (str)\n  - contextId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
            'contextId': contextId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/field/{fieldId}/context/{contextId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_custom_field_context(
        self,
        fieldId: str,
        contextId: int,
        description: Optional[str] = None,
        name: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update custom field context\n\nHTTP PUT /rest/api/3/field/{fieldId}/context/{contextId}\nPath params:\n  - fieldId (str)\n  - contextId (int)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
            'contextId': contextId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        rel_path = '/rest/api/3/field/{fieldId}/context/{contextId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_issue_types_to_context(
        self,
        fieldId: str,
        contextId: int,
        issueTypeIds: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add issue types to context\n\nHTTP PUT /rest/api/3/field/{fieldId}/context/{contextId}/issuetype\nPath params:\n  - fieldId (str)\n  - contextId (int)\nBody (application/json) fields:\n  - issueTypeIds (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
            'contextId': contextId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['issueTypeIds'] = issueTypeIds
        rel_path = '/rest/api/3/field/{fieldId}/context/{contextId}/issuetype'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_issue_types_from_context(
        self,
        fieldId: str,
        contextId: int,
        issueTypeIds: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove issue types from context\n\nHTTP POST /rest/api/3/field/{fieldId}/context/{contextId}/issuetype/remove\nPath params:\n  - fieldId (str)\n  - contextId (int)\nBody (application/json) fields:\n  - issueTypeIds (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
            'contextId': contextId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['issueTypeIds'] = issueTypeIds
        rel_path = '/rest/api/3/field/{fieldId}/context/{contextId}/issuetype/remove'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_options_for_context(
        self,
        fieldId: str,
        contextId: int,
        optionId: Optional[int] = None,
        onlyOptions: Optional[bool] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom field options (context)\n\nHTTP GET /rest/api/3/field/{fieldId}/context/{contextId}/option\nPath params:\n  - fieldId (str)\n  - contextId (int)\nQuery params:\n  - optionId (int, optional)\n  - onlyOptions (bool, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
            'contextId': contextId,
        }
        _query: Dict[str, Any] = {}
        if optionId is not None:
            _query['optionId'] = optionId
        if onlyOptions is not None:
            _query['onlyOptions'] = onlyOptions
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/field/{fieldId}/context/{contextId}/option'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_custom_field_option(
        self,
        fieldId: str,
        contextId: int,
        options: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create custom field options (context)\n\nHTTP POST /rest/api/3/field/{fieldId}/context/{contextId}/option\nPath params:\n  - fieldId (str)\n  - contextId (int)\nBody (application/json) fields:\n  - options (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
            'contextId': contextId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if options is not None:
            _body['options'] = options
        rel_path = '/rest/api/3/field/{fieldId}/context/{contextId}/option'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_custom_field_option(
        self,
        fieldId: str,
        contextId: int,
        options: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update custom field options (context)\n\nHTTP PUT /rest/api/3/field/{fieldId}/context/{contextId}/option\nPath params:\n  - fieldId (str)\n  - contextId (int)\nBody (application/json) fields:\n  - options (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
            'contextId': contextId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if options is not None:
            _body['options'] = options
        rel_path = '/rest/api/3/field/{fieldId}/context/{contextId}/option'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def reorder_custom_field_options(
        self,
        fieldId: str,
        contextId: int,
        customFieldOptionIds: list[str],
        after: Optional[str] = None,
        position: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Reorder custom field options (context)\n\nHTTP PUT /rest/api/3/field/{fieldId}/context/{contextId}/option/move\nPath params:\n  - fieldId (str)\n  - contextId (int)\nBody (application/json) fields:\n  - after (str, optional)\n  - customFieldOptionIds (list[str], required)\n  - position (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
            'contextId': contextId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if after is not None:
            _body['after'] = after
        _body['customFieldOptionIds'] = customFieldOptionIds
        if position is not None:
            _body['position'] = position
        rel_path = '/rest/api/3/field/{fieldId}/context/{contextId}/option/move'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_custom_field_option(
        self,
        fieldId: str,
        contextId: int,
        optionId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete custom field options (context)\n\nHTTP DELETE /rest/api/3/field/{fieldId}/context/{contextId}/option/{optionId}\nPath params:\n  - fieldId (str)\n  - contextId (int)\n  - optionId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
            'contextId': contextId,
            'optionId': optionId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/field/{fieldId}/context/{contextId}/option/{optionId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def replace_custom_field_option(
        self,
        fieldId: str,
        optionId: int,
        contextId: int,
        replaceWith: Optional[int] = None,
        jql: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Replace custom field options\n\nHTTP DELETE /rest/api/3/field/{fieldId}/context/{contextId}/option/{optionId}/issue\nPath params:\n  - fieldId (str)\n  - optionId (int)\n  - contextId (int)\nQuery params:\n  - replaceWith (int, optional)\n  - jql (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
            'optionId': optionId,
            'contextId': contextId,
        }
        _query: Dict[str, Any] = {}
        if replaceWith is not None:
            _query['replaceWith'] = replaceWith
        if jql is not None:
            _query['jql'] = jql
        _body = None
        rel_path = '/rest/api/3/field/{fieldId}/context/{contextId}/option/{optionId}/issue'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def assign_projects_to_custom_field_context(
        self,
        fieldId: str,
        contextId: int,
        projectIds: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Assign custom field context to projects\n\nHTTP PUT /rest/api/3/field/{fieldId}/context/{contextId}/project\nPath params:\n  - fieldId (str)\n  - contextId (int)\nBody (application/json) fields:\n  - projectIds (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
            'contextId': contextId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['projectIds'] = projectIds
        rel_path = '/rest/api/3/field/{fieldId}/context/{contextId}/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_custom_field_context_from_projects(
        self,
        fieldId: str,
        contextId: int,
        projectIds: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove custom field context from projects\n\nHTTP POST /rest/api/3/field/{fieldId}/context/{contextId}/project/remove\nPath params:\n  - fieldId (str)\n  - contextId (int)\nBody (application/json) fields:\n  - projectIds (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
            'contextId': contextId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['projectIds'] = projectIds
        rel_path = '/rest/api/3/field/{fieldId}/context/{contextId}/project/remove'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_contexts_for_field_deprecated(
        self,
        fieldId: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get contexts for a field\n\nHTTP GET /rest/api/3/field/{fieldId}/contexts\nPath params:\n  - fieldId (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/field/{fieldId}/contexts'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_screens_for_field(
        self,
        fieldId: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get screens for a field\n\nHTTP GET /rest/api/3/field/{fieldId}/screens\nPath params:\n  - fieldId (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/field/{fieldId}/screens'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_issue_field_options(
        self,
        fieldKey: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all issue field options\n\nHTTP GET /rest/api/3/field/{fieldKey}/option\nPath params:\n  - fieldKey (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldKey': fieldKey,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/field/{fieldKey}/option'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_issue_field_option(
        self,
        fieldKey: str,
        value: str,
        config: Optional[Dict[str, Any]] = None,
        properties: Optional[Dict[str, Any]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create issue field option\n\nHTTP POST /rest/api/3/field/{fieldKey}/option\nPath params:\n  - fieldKey (str)\nBody (application/json) fields:\n  - config (Dict[str, Any], optional)\n  - properties (Dict[str, Any], optional)\n  - value (str, required)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldKey': fieldKey,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if config is not None:
            _body['config'] = config
        if properties is not None:
            _body['properties'] = properties
        _body['value'] = value
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/field/{fieldKey}/option'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_selectable_issue_field_options(
        self,
        fieldKey: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        projectId: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get selectable issue field options\n\nHTTP GET /rest/api/3/field/{fieldKey}/option/suggestions/edit\nPath params:\n  - fieldKey (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - projectId (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldKey': fieldKey,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if projectId is not None:
            _query['projectId'] = projectId
        _body = None
        rel_path = '/rest/api/3/field/{fieldKey}/option/suggestions/edit'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_visible_issue_field_options(
        self,
        fieldKey: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        projectId: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get visible issue field options\n\nHTTP GET /rest/api/3/field/{fieldKey}/option/suggestions/search\nPath params:\n  - fieldKey (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - projectId (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldKey': fieldKey,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if projectId is not None:
            _query['projectId'] = projectId
        _body = None
        rel_path = '/rest/api/3/field/{fieldKey}/option/suggestions/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_issue_field_option(
        self,
        fieldKey: str,
        optionId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete issue field option\n\nHTTP DELETE /rest/api/3/field/{fieldKey}/option/{optionId}\nPath params:\n  - fieldKey (str)\n  - optionId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldKey': fieldKey,
            'optionId': optionId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/field/{fieldKey}/option/{optionId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_field_option(
        self,
        fieldKey: str,
        optionId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue field option\n\nHTTP GET /rest/api/3/field/{fieldKey}/option/{optionId}\nPath params:\n  - fieldKey (str)\n  - optionId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldKey': fieldKey,
            'optionId': optionId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/field/{fieldKey}/option/{optionId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_issue_field_option(
        self,
        fieldKey: str,
        optionId: int,
        id: int,
        value: str,
        config: Optional[Dict[str, Any]] = None,
        properties: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update issue field option\n\nHTTP PUT /rest/api/3/field/{fieldKey}/option/{optionId}\nPath params:\n  - fieldKey (str)\n  - optionId (int)\nBody (application/json) fields:\n  - config (Dict[str, Any], optional)\n  - id (int, required)\n  - properties (Dict[str, Any], optional)\n  - value (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'fieldKey': fieldKey,
            'optionId': optionId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if config is not None:
            _body['config'] = config
        _body['id'] = id
        if properties is not None:
            _body['properties'] = properties
        _body['value'] = value
        rel_path = '/rest/api/3/field/{fieldKey}/option/{optionId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def replace_issue_field_option(
        self,
        fieldKey: str,
        optionId: int,
        replaceWith: Optional[int] = None,
        jql: Optional[str] = None,
        overrideScreenSecurity: Optional[bool] = None,
        overrideEditableFlag: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Replace issue field option\n\nHTTP DELETE /rest/api/3/field/{fieldKey}/option/{optionId}/issue\nPath params:\n  - fieldKey (str)\n  - optionId (int)\nQuery params:\n  - replaceWith (int, optional)\n  - jql (str, optional)\n  - overrideScreenSecurity (bool, optional)\n  - overrideEditableFlag (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldKey': fieldKey,
            'optionId': optionId,
        }
        _query: Dict[str, Any] = {}
        if replaceWith is not None:
            _query['replaceWith'] = replaceWith
        if jql is not None:
            _query['jql'] = jql
        if overrideScreenSecurity is not None:
            _query['overrideScreenSecurity'] = overrideScreenSecurity
        if overrideEditableFlag is not None:
            _query['overrideEditableFlag'] = overrideEditableFlag
        _body = None
        rel_path = '/rest/api/3/field/{fieldKey}/option/{optionId}/issue'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_custom_field(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete custom field\n\nHTTP DELETE /rest/api/3/field/{id}\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/field/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def restore_custom_field(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Restore custom field from trash\n\nHTTP POST /rest/api/3/field/{id}/restore\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/field/{id}/restore'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def trash_custom_field(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Move custom field to trash\n\nHTTP POST /rest/api/3/field/{id}/trash\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/field/{id}/trash'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_field_configurations(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        id: Optional[list[int]] = None,
        isDefault: Optional[bool] = None,
        query: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all field configurations\n\nHTTP GET /rest/api/3/fieldconfiguration\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - id (list[int], optional)\n  - isDefault (bool, optional)\n  - query (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        if isDefault is not None:
            _query['isDefault'] = isDefault
        if query is not None:
            _query['query'] = query
        _body = None
        rel_path = '/rest/api/3/fieldconfiguration'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_field_configuration(
        self,
        name: str,
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create field configuration\n\nHTTP POST /rest/api/3/fieldconfiguration\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['name'] = name
        rel_path = '/rest/api/3/fieldconfiguration'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_field_configuration(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete field configuration\n\nHTTP DELETE /rest/api/3/fieldconfiguration/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/fieldconfiguration/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_field_configuration(
        self,
        id: int,
        name: str,
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update field configuration\n\nHTTP PUT /rest/api/3/fieldconfiguration/{id}\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['name'] = name
        rel_path = '/rest/api/3/fieldconfiguration/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_field_configuration_items(
        self,
        id: int,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get field configuration items\n\nHTTP GET /rest/api/3/fieldconfiguration/{id}/fields\nPath params:\n  - id (int)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/fieldconfiguration/{id}/fields'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_field_configuration_items(
        self,
        id: int,
        fieldConfigurationItems: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update field configuration items\n\nHTTP PUT /rest/api/3/fieldconfiguration/{id}/fields\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - fieldConfigurationItems (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['fieldConfigurationItems'] = fieldConfigurationItems
        rel_path = '/rest/api/3/fieldconfiguration/{id}/fields'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_field_configuration_schemes(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        id: Optional[list[int]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all field configuration schemes\n\nHTTP GET /rest/api/3/fieldconfigurationscheme\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - id (list[int], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        _body = None
        rel_path = '/rest/api/3/fieldconfigurationscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_field_configuration_scheme(
        self,
        name: str,
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create field configuration scheme\n\nHTTP POST /rest/api/3/fieldconfigurationscheme\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['name'] = name
        rel_path = '/rest/api/3/fieldconfigurationscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_field_configuration_scheme_mappings(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        fieldConfigurationSchemeId: Optional[list[int]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get field configuration issue type items\n\nHTTP GET /rest/api/3/fieldconfigurationscheme/mapping\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - fieldConfigurationSchemeId (list[int], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if fieldConfigurationSchemeId is not None:
            _query['fieldConfigurationSchemeId'] = fieldConfigurationSchemeId
        _body = None
        rel_path = '/rest/api/3/fieldconfigurationscheme/mapping'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_field_configuration_scheme_project_mapping(
        self,
        projectId: list[int],
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get field configuration schemes for projects\n\nHTTP GET /rest/api/3/fieldconfigurationscheme/project\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - projectId (list[int], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _query['projectId'] = projectId
        _body = None
        rel_path = '/rest/api/3/fieldconfigurationscheme/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def assign_field_configuration_scheme_to_project(
        self,
        projectId: str,
        fieldConfigurationSchemeId: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Assign field configuration scheme to project\n\nHTTP PUT /rest/api/3/fieldconfigurationscheme/project\nBody (application/json) fields:\n  - fieldConfigurationSchemeId (str, optional)\n  - projectId (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if fieldConfigurationSchemeId is not None:
            _body['fieldConfigurationSchemeId'] = fieldConfigurationSchemeId
        _body['projectId'] = projectId
        rel_path = '/rest/api/3/fieldconfigurationscheme/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_field_configuration_scheme(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete field configuration scheme\n\nHTTP DELETE /rest/api/3/fieldconfigurationscheme/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/fieldconfigurationscheme/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_field_configuration_scheme(
        self,
        id: int,
        name: str,
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update field configuration scheme\n\nHTTP PUT /rest/api/3/fieldconfigurationscheme/{id}\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['name'] = name
        rel_path = '/rest/api/3/fieldconfigurationscheme/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_field_configuration_scheme_mapping(
        self,
        id: int,
        mappings: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Assign issue types to field configurations\n\nHTTP PUT /rest/api/3/fieldconfigurationscheme/{id}/mapping\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - mappings (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['mappings'] = mappings
        rel_path = '/rest/api/3/fieldconfigurationscheme/{id}/mapping'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_issue_types_from_global_field_configuration_scheme(
        self,
        id: int,
        issueTypeIds: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove issue types from field configuration scheme\n\nHTTP POST /rest/api/3/fieldconfigurationscheme/{id}/mapping/delete\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - issueTypeIds (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['issueTypeIds'] = issueTypeIds
        rel_path = '/rest/api/3/fieldconfigurationscheme/{id}/mapping/delete'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_filter(
        self,
        name: str,
        expand: Optional[str] = None,
        overrideSharePermissions: Optional[bool] = None,
        approximateLastUsed: Optional[str] = None,
        description: Optional[str] = None,
        editPermissions: Optional[list[Dict[str, Any]]] = None,
        favourite: Optional[bool] = None,
        favouritedCount: Optional[int] = None,
        id: Optional[str] = None,
        jql: Optional[str] = None,
        owner: Optional[Dict[str, Any]] = None,
        searchUrl: Optional[str] = None,
        self_: Optional[str] = None,
        sharePermissions: Optional[list[Dict[str, Any]]] = None,
        sharedUsers: Optional[Dict[str, Any]] = None,
        subscriptions: Optional[Dict[str, Any]] = None,
        viewUrl: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create filter\n\nHTTP POST /rest/api/3/filter\nQuery params:\n  - expand (str, optional)\n  - overrideSharePermissions (bool, optional)\nBody (application/json) fields:\n  - approximateLastUsed (str, optional)\n  - description (str, optional)\n  - editPermissions (list[Dict[str, Any]], optional)\n  - favourite (bool, optional)\n  - favouritedCount (int, optional)\n  - id (str, optional)\n  - jql (str, optional)\n  - name (str, required)\n  - owner (Dict[str, Any], optional)\n  - searchUrl (str, optional)\n  - self (str, optional)\n  - sharePermissions (list[Dict[str, Any]], optional)\n  - sharedUsers (Dict[str, Any], optional)\n  - subscriptions (Dict[str, Any], optional)\n  - viewUrl (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        if overrideSharePermissions is not None:
            _query['overrideSharePermissions'] = overrideSharePermissions
        _body: Dict[str, Any] = {}
        if approximateLastUsed is not None:
            _body['approximateLastUsed'] = approximateLastUsed
        if description is not None:
            _body['description'] = description
        if editPermissions is not None:
            _body['editPermissions'] = editPermissions
        if favourite is not None:
            _body['favourite'] = favourite
        if favouritedCount is not None:
            _body['favouritedCount'] = favouritedCount
        if id is not None:
            _body['id'] = id
        if jql is not None:
            _body['jql'] = jql
        _body['name'] = name
        if owner is not None:
            _body['owner'] = owner
        if searchUrl is not None:
            _body['searchUrl'] = searchUrl
        if self_ is not None:
            _body['self'] = self_
        if sharePermissions is not None:
            _body['sharePermissions'] = sharePermissions
        if sharedUsers is not None:
            _body['sharedUsers'] = sharedUsers
        if subscriptions is not None:
            _body['subscriptions'] = subscriptions
        if viewUrl is not None:
            _body['viewUrl'] = viewUrl
        rel_path = '/rest/api/3/filter'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_default_share_scope(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get default share scope\n\nHTTP GET /rest/api/3/filter/defaultShareScope"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/filter/defaultShareScope'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_default_share_scope(
        self,
        scope: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set default share scope\n\nHTTP PUT /rest/api/3/filter/defaultShareScope\nBody (application/json) fields:\n  - scope (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['scope'] = scope
        rel_path = '/rest/api/3/filter/defaultShareScope'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_favourite_filters(
        self,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get favorite filters\n\nHTTP GET /rest/api/3/filter/favourite\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/filter/favourite'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_my_filters(
        self,
        expand: Optional[str] = None,
        includeFavourites: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get my filters\n\nHTTP GET /rest/api/3/filter/my\nQuery params:\n  - expand (str, optional)\n  - includeFavourites (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        if includeFavourites is not None:
            _query['includeFavourites'] = includeFavourites
        _body = None
        rel_path = '/rest/api/3/filter/my'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_filters_paginated(
        self,
        filterName: Optional[str] = None,
        accountId: Optional[str] = None,
        owner: Optional[str] = None,
        groupname: Optional[str] = None,
        groupId: Optional[str] = None,
        projectId: Optional[int] = None,
        id: Optional[list[int]] = None,
        orderBy: Optional[str] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        expand: Optional[str] = None,
        overrideSharePermissions: Optional[bool] = None,
        isSubstringMatch: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Search for filters\n\nHTTP GET /rest/api/3/filter/search\nQuery params:\n  - filterName (str, optional)\n  - accountId (str, optional)\n  - owner (str, optional)\n  - groupname (str, optional)\n  - groupId (str, optional)\n  - projectId (int, optional)\n  - id (list[int], optional)\n  - orderBy (str, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - expand (str, optional)\n  - overrideSharePermissions (bool, optional)\n  - isSubstringMatch (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if filterName is not None:
            _query['filterName'] = filterName
        if accountId is not None:
            _query['accountId'] = accountId
        if owner is not None:
            _query['owner'] = owner
        if groupname is not None:
            _query['groupname'] = groupname
        if groupId is not None:
            _query['groupId'] = groupId
        if projectId is not None:
            _query['projectId'] = projectId
        if id is not None:
            _query['id'] = id
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if expand is not None:
            _query['expand'] = expand
        if overrideSharePermissions is not None:
            _query['overrideSharePermissions'] = overrideSharePermissions
        if isSubstringMatch is not None:
            _query['isSubstringMatch'] = isSubstringMatch
        _body = None
        rel_path = '/rest/api/3/filter/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_filter(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete filter\n\nHTTP DELETE /rest/api/3/filter/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/filter/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_filter(
        self,
        id: int,
        expand: Optional[str] = None,
        overrideSharePermissions: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get filter\n\nHTTP GET /rest/api/3/filter/{id}\nPath params:\n  - id (int)\nQuery params:\n  - expand (str, optional)\n  - overrideSharePermissions (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        if overrideSharePermissions is not None:
            _query['overrideSharePermissions'] = overrideSharePermissions
        _body = None
        rel_path = '/rest/api/3/filter/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_filter(
        self,
        id: int,
        name: str,
        expand: Optional[str] = None,
        overrideSharePermissions: Optional[bool] = None,
        approximateLastUsed: Optional[str] = None,
        description: Optional[str] = None,
        editPermissions: Optional[list[Dict[str, Any]]] = None,
        favourite: Optional[bool] = None,
        favouritedCount: Optional[int] = None,
        id_body: Optional[str] = None,
        jql: Optional[str] = None,
        owner: Optional[Dict[str, Any]] = None,
        searchUrl: Optional[str] = None,
        self_: Optional[str] = None,
        sharePermissions: Optional[list[Dict[str, Any]]] = None,
        sharedUsers: Optional[Dict[str, Any]] = None,
        subscriptions: Optional[Dict[str, Any]] = None,
        viewUrl: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update filter\n\nHTTP PUT /rest/api/3/filter/{id}\nPath params:\n  - id (int)\nQuery params:\n  - expand (str, optional)\n  - overrideSharePermissions (bool, optional)\nBody (application/json) fields:\n  - approximateLastUsed (str, optional)\n  - description (str, optional)\n  - editPermissions (list[Dict[str, Any]], optional)\n  - favourite (bool, optional)\n  - favouritedCount (int, optional)\n  - id (str, optional)\n  - jql (str, optional)\n  - name (str, required)\n  - owner (Dict[str, Any], optional)\n  - searchUrl (str, optional)\n  - self (str, optional)\n  - sharePermissions (list[Dict[str, Any]], optional)\n  - sharedUsers (Dict[str, Any], optional)\n  - subscriptions (Dict[str, Any], optional)\n  - viewUrl (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        if overrideSharePermissions is not None:
            _query['overrideSharePermissions'] = overrideSharePermissions
        _body: Dict[str, Any] = {}
        if approximateLastUsed is not None:
            _body['approximateLastUsed'] = approximateLastUsed
        if description is not None:
            _body['description'] = description
        if editPermissions is not None:
            _body['editPermissions'] = editPermissions
        if favourite is not None:
            _body['favourite'] = favourite
        if favouritedCount is not None:
            _body['favouritedCount'] = favouritedCount
        if id_body is not None:
            _body['id'] = id_body
        if jql is not None:
            _body['jql'] = jql
        _body['name'] = name
        if owner is not None:
            _body['owner'] = owner
        if searchUrl is not None:
            _body['searchUrl'] = searchUrl
        if self_ is not None:
            _body['self'] = self_
        if sharePermissions is not None:
            _body['sharePermissions'] = sharePermissions
        if sharedUsers is not None:
            _body['sharedUsers'] = sharedUsers
        if subscriptions is not None:
            _body['subscriptions'] = subscriptions
        if viewUrl is not None:
            _body['viewUrl'] = viewUrl
        rel_path = '/rest/api/3/filter/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def reset_columns(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Reset columns\n\nHTTP DELETE /rest/api/3/filter/{id}/columns\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/filter/{id}/columns'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_columns(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get columns\n\nHTTP GET /rest/api/3/filter/{id}/columns\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/filter/{id}/columns'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_columns(
        self,
        id: int,
        columns: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set columns\n\nHTTP PUT /rest/api/3/filter/{id}/columns\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - columns (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if columns is not None:
            _body['columns'] = columns
        rel_path = '/rest/api/3/filter/{id}/columns'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_favourite_for_filter(
        self,
        id: int,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove filter as favorite\n\nHTTP DELETE /rest/api/3/filter/{id}/favourite\nPath params:\n  - id (int)\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/filter/{id}/favourite'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_favourite_for_filter(
        self,
        id: int,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add filter as favorite\n\nHTTP PUT /rest/api/3/filter/{id}/favourite\nPath params:\n  - id (int)\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/filter/{id}/favourite'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def change_filter_owner(
        self,
        id: int,
        accountId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Change filter owner\n\nHTTP PUT /rest/api/3/filter/{id}/owner\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - accountId (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['accountId'] = accountId
        rel_path = '/rest/api/3/filter/{id}/owner'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_share_permissions(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get share permissions\n\nHTTP GET /rest/api/3/filter/{id}/permission\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/filter/{id}/permission'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_share_permission(
        self,
        id: int,
        type: str,
        accountId: Optional[str] = None,
        groupId: Optional[str] = None,
        groupname: Optional[str] = None,
        projectId: Optional[str] = None,
        projectRoleId: Optional[str] = None,
        rights: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add share permission\n\nHTTP POST /rest/api/3/filter/{id}/permission\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - accountId (str, optional)\n  - groupId (str, optional)\n  - groupname (str, optional)\n  - projectId (str, optional)\n  - projectRoleId (str, optional)\n  - rights (int, optional)\n  - type (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if accountId is not None:
            _body['accountId'] = accountId
        if groupId is not None:
            _body['groupId'] = groupId
        if groupname is not None:
            _body['groupname'] = groupname
        if projectId is not None:
            _body['projectId'] = projectId
        if projectRoleId is not None:
            _body['projectRoleId'] = projectRoleId
        if rights is not None:
            _body['rights'] = rights
        _body['type'] = type
        rel_path = '/rest/api/3/filter/{id}/permission'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_share_permission(
        self,
        id: int,
        permissionId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete share permission\n\nHTTP DELETE /rest/api/3/filter/{id}/permission/{permissionId}\nPath params:\n  - id (int)\n  - permissionId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
            'permissionId': permissionId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/filter/{id}/permission/{permissionId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_share_permission(
        self,
        id: int,
        permissionId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get share permission\n\nHTTP GET /rest/api/3/filter/{id}/permission/{permissionId}\nPath params:\n  - id (int)\n  - permissionId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
            'permissionId': permissionId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/filter/{id}/permission/{permissionId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_group(
        self,
        groupname: Optional[str] = None,
        groupId: Optional[str] = None,
        swapGroup: Optional[str] = None,
        swapGroupId: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove group\n\nHTTP DELETE /rest/api/3/group\nQuery params:\n  - groupname (str, optional)\n  - groupId (str, optional)\n  - swapGroup (str, optional)\n  - swapGroupId (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if groupname is not None:
            _query['groupname'] = groupname
        if groupId is not None:
            _query['groupId'] = groupId
        if swapGroup is not None:
            _query['swapGroup'] = swapGroup
        if swapGroupId is not None:
            _query['swapGroupId'] = swapGroupId
        _body = None
        rel_path = '/rest/api/3/group'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_group(
        self,
        groupname: Optional[str] = None,
        groupId: Optional[str] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get group\n\nHTTP GET /rest/api/3/group\nQuery params:\n  - groupname (str, optional)\n  - groupId (str, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if groupname is not None:
            _query['groupname'] = groupname
        if groupId is not None:
            _query['groupId'] = groupId
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/group'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_group(
        self,
        name: str,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create group\n\nHTTP POST /rest/api/3/group\nBody (application/json) fields:\n  - name (str, required)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['name'] = name
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/group'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def bulk_get_groups(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        groupId: Optional[list[str]] = None,
        groupName: Optional[list[str]] = None,
        accessType: Optional[str] = None,
        applicationKey: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk get groups\n\nHTTP GET /rest/api/3/group/bulk\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - groupId (list[str], optional)\n  - groupName (list[str], optional)\n  - accessType (str, optional)\n  - applicationKey (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if groupId is not None:
            _query['groupId'] = groupId
        if groupName is not None:
            _query['groupName'] = groupName
        if accessType is not None:
            _query['accessType'] = accessType
        if applicationKey is not None:
            _query['applicationKey'] = applicationKey
        _body = None
        rel_path = '/rest/api/3/group/bulk'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_users_from_group(
        self,
        groupname: Optional[str] = None,
        groupId: Optional[str] = None,
        includeInactiveUsers: Optional[bool] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get users from group\n\nHTTP GET /rest/api/3/group/member\nQuery params:\n  - groupname (str, optional)\n  - groupId (str, optional)\n  - includeInactiveUsers (bool, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if groupname is not None:
            _query['groupname'] = groupname
        if groupId is not None:
            _query['groupId'] = groupId
        if includeInactiveUsers is not None:
            _query['includeInactiveUsers'] = includeInactiveUsers
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/group/member'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_user_from_group(
        self,
        accountId: str,
        groupname: Optional[str] = None,
        groupId: Optional[str] = None,
        username: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove user from group\n\nHTTP DELETE /rest/api/3/group/user\nQuery params:\n  - groupname (str, optional)\n  - groupId (str, optional)\n  - username (str, optional)\n  - accountId (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if groupname is not None:
            _query['groupname'] = groupname
        if groupId is not None:
            _query['groupId'] = groupId
        if username is not None:
            _query['username'] = username
        _query['accountId'] = accountId
        _body = None
        rel_path = '/rest/api/3/group/user'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_user_to_group(
        self,
        groupname: Optional[str] = None,
        groupId: Optional[str] = None,
        accountId: Optional[str] = None,
        name: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add user to group\n\nHTTP POST /rest/api/3/group/user\nQuery params:\n  - groupname (str, optional)\n  - groupId (str, optional)\nBody (application/json) fields:\n  - accountId (str, optional)\n  - name (str, optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if groupname is not None:
            _query['groupname'] = groupname
        if groupId is not None:
            _query['groupId'] = groupId
        _body: Dict[str, Any] = {}
        if accountId is not None:
            _body['accountId'] = accountId
        if name is not None:
            _body['name'] = name
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/group/user'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def find_groups(
        self,
        accountId: Optional[str] = None,
        query: Optional[str] = None,
        exclude: Optional[list[str]] = None,
        excludeId: Optional[list[str]] = None,
        maxResults: Optional[int] = None,
        caseInsensitive: Optional[bool] = None,
        userName: Optional[str] = None,
        includeTeams: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Find groups\n\nHTTP GET /rest/api/3/groups/picker\nQuery params:\n  - accountId (str, optional)\n  - query (str, optional)\n  - exclude (list[str], optional)\n  - excludeId (list[str], optional)\n  - maxResults (int, optional)\n  - caseInsensitive (bool, optional)\n  - userName (str, optional)\n  - includeTeams (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if accountId is not None:
            _query['accountId'] = accountId
        if query is not None:
            _query['query'] = query
        if exclude is not None:
            _query['exclude'] = exclude
        if excludeId is not None:
            _query['excludeId'] = excludeId
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if caseInsensitive is not None:
            _query['caseInsensitive'] = caseInsensitive
        if userName is not None:
            _query['userName'] = userName
        if includeTeams is not None:
            _query['includeTeams'] = includeTeams
        _body = None
        rel_path = '/rest/api/3/groups/picker'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def find_users_and_groups(
        self,
        query: str,
        maxResults: Optional[int] = None,
        showAvatar: Optional[bool] = None,
        fieldId: Optional[str] = None,
        projectId: Optional[list[str]] = None,
        issueTypeId: Optional[list[str]] = None,
        avatarSize: Optional[str] = None,
        caseInsensitive: Optional[bool] = None,
        excludeConnectAddons: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Find users and groups\n\nHTTP GET /rest/api/3/groupuserpicker\nQuery params:\n  - query (str, required)\n  - maxResults (int, optional)\n  - showAvatar (bool, optional)\n  - fieldId (str, optional)\n  - projectId (list[str], optional)\n  - issueTypeId (list[str], optional)\n  - avatarSize (str, optional)\n  - caseInsensitive (bool, optional)\n  - excludeConnectAddons (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['query'] = query
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if showAvatar is not None:
            _query['showAvatar'] = showAvatar
        if fieldId is not None:
            _query['fieldId'] = fieldId
        if projectId is not None:
            _query['projectId'] = projectId
        if issueTypeId is not None:
            _query['issueTypeId'] = issueTypeId
        if avatarSize is not None:
            _query['avatarSize'] = avatarSize
        if caseInsensitive is not None:
            _query['caseInsensitive'] = caseInsensitive
        if excludeConnectAddons is not None:
            _query['excludeConnectAddons'] = excludeConnectAddons
        _body = None
        rel_path = '/rest/api/3/groupuserpicker'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_license(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get license\n\nHTTP GET /rest/api/3/instance/license"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/instance/license'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_issue(
        self,
        updateHistory: Optional[bool] = None,
        fields: Optional[Dict[str, Any]] = None,
        historyMetadata: Optional[Dict[str, Any]] = None,
        properties: Optional[list[Dict[str, Any]]] = None,
        transition: Optional[Dict[str, Any]] = None,
        update: Optional[Dict[str, Any]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create issue\n\nHTTP POST /rest/api/3/issue\nQuery params:\n  - updateHistory (bool, optional)\nBody (application/json) fields:\n  - fields (Dict[str, Any], optional)\n  - historyMetadata (Dict[str, Any], optional)\n  - properties (list[Dict[str, Any]], optional)\n  - transition (Dict[str, Any], optional)\n  - update (Dict[str, Any], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if updateHistory is not None:
            _query['updateHistory'] = updateHistory
        _body: Dict[str, Any] = {}
        if fields is not None:
            _body['fields'] = fields
        if historyMetadata is not None:
            _body['historyMetadata'] = historyMetadata
        if properties is not None:
            _body['properties'] = properties
        if transition is not None:
            _body['transition'] = transition
        if update is not None:
            _body['update'] = update
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issue'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def archive_issues_async(
        self,
        jql: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Archive issue(s) by JQL\n\nHTTP POST /rest/api/3/issue/archive\nBody (application/json) fields:\n  - jql (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if jql is not None:
            _body['jql'] = jql
        rel_path = '/rest/api/3/issue/archive'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def archive_issues(
        self,
        issueIdsOrKeys: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Archive issue(s) by issue ID/key\n\nHTTP PUT /rest/api/3/issue/archive\nBody (application/json) fields:\n  - issueIdsOrKeys (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if issueIdsOrKeys is not None:
            _body['issueIdsOrKeys'] = issueIdsOrKeys
        rel_path = '/rest/api/3/issue/archive'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_issues(
        self,
        issueUpdates: Optional[list[Dict[str, Any]]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk create issue\n\nHTTP POST /rest/api/3/issue/bulk\nBody (application/json) fields:\n  - issueUpdates (list[Dict[str, Any]], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if issueUpdates is not None:
            _body['issueUpdates'] = issueUpdates
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issue/bulk'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def bulk_fetch_issues(
        self,
        issueIdsOrKeys: list[str],
        expand: Optional[list[str]] = None,
        fields: Optional[list[str]] = None,
        fieldsByKeys: Optional[bool] = None,
        properties: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk fetch issues\n\nHTTP POST /rest/api/3/issue/bulkfetch\nBody (application/json) fields:\n  - expand (list[str], optional)\n  - fields (list[str], optional)\n  - fieldsByKeys (bool, optional)\n  - issueIdsOrKeys (list[str], required)\n  - properties (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if expand is not None:
            _body['expand'] = expand
        if fields is not None:
            _body['fields'] = fields
        if fieldsByKeys is not None:
            _body['fieldsByKeys'] = fieldsByKeys
        _body['issueIdsOrKeys'] = issueIdsOrKeys
        if properties is not None:
            _body['properties'] = properties
        rel_path = '/rest/api/3/issue/bulkfetch'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_create_issue_meta(
        self,
        projectIds: Optional[list[str]] = None,
        projectKeys: Optional[list[str]] = None,
        issuetypeIds: Optional[list[str]] = None,
        issuetypeNames: Optional[list[str]] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get create issue metadata\n\nHTTP GET /rest/api/3/issue/createmeta\nQuery params:\n  - projectIds (list[str], optional)\n  - projectKeys (list[str], optional)\n  - issuetypeIds (list[str], optional)\n  - issuetypeNames (list[str], optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if projectIds is not None:
            _query['projectIds'] = projectIds
        if projectKeys is not None:
            _query['projectKeys'] = projectKeys
        if issuetypeIds is not None:
            _query['issuetypeIds'] = issuetypeIds
        if issuetypeNames is not None:
            _query['issuetypeNames'] = issuetypeNames
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/issue/createmeta'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_create_issue_meta_issue_types(
        self,
        projectIdOrKey: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get create metadata issue types for a project\n\nHTTP GET /rest/api/3/issue/createmeta/{projectIdOrKey}/issuetypes\nPath params:\n  - projectIdOrKey (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/issue/createmeta/{projectIdOrKey}/issuetypes'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_create_issue_meta_issue_type_id(
        self,
        projectIdOrKey: str,
        issueTypeId: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get create field metadata for a project and issue type id\n\nHTTP GET /rest/api/3/issue/createmeta/{projectIdOrKey}/issuetypes/{issueTypeId}\nPath params:\n  - projectIdOrKey (str)\n  - issueTypeId (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
            'issueTypeId': issueTypeId,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/issue/createmeta/{projectIdOrKey}/issuetypes/{issueTypeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_limit_report(
        self,
        isReturningKeys: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue limit report\n\nHTTP GET /rest/api/3/issue/limit/report\nQuery params:\n  - isReturningKeys (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if isReturningKeys is not None:
            _query['isReturningKeys'] = isReturningKeys
        _body = None
        rel_path = '/rest/api/3/issue/limit/report'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_picker_resource(
        self,
        query: Optional[str] = None,
        currentJQL: Optional[str] = None,
        currentIssueKey: Optional[str] = None,
        currentProjectId: Optional[str] = None,
        showSubTasks: Optional[bool] = None,
        showSubTaskParent: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue picker suggestions\n\nHTTP GET /rest/api/3/issue/picker\nQuery params:\n  - query (str, optional)\n  - currentJQL (str, optional)\n  - currentIssueKey (str, optional)\n  - currentProjectId (str, optional)\n  - showSubTasks (bool, optional)\n  - showSubTaskParent (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if query is not None:
            _query['query'] = query
        if currentJQL is not None:
            _query['currentJQL'] = currentJQL
        if currentIssueKey is not None:
            _query['currentIssueKey'] = currentIssueKey
        if currentProjectId is not None:
            _query['currentProjectId'] = currentProjectId
        if showSubTasks is not None:
            _query['showSubTasks'] = showSubTasks
        if showSubTaskParent is not None:
            _query['showSubTaskParent'] = showSubTaskParent
        _body = None
        rel_path = '/rest/api/3/issue/picker'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def bulk_set_issues_properties_list(
        self,
        entitiesIds: Optional[list[int]] = None,
        properties: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk set issues properties by list\n\nHTTP POST /rest/api/3/issue/properties\nBody (application/json) fields:\n  - entitiesIds (list[int], optional)\n  - properties (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if entitiesIds is not None:
            _body['entitiesIds'] = entitiesIds
        if properties is not None:
            _body['properties'] = properties
        rel_path = '/rest/api/3/issue/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def bulk_set_issue_properties_by_issue(
        self,
        issues: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk set issue properties by issue\n\nHTTP POST /rest/api/3/issue/properties/multi\nBody (application/json) fields:\n  - issues (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if issues is not None:
            _body['issues'] = issues
        rel_path = '/rest/api/3/issue/properties/multi'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def bulk_delete_issue_property(
        self,
        propertyKey: str,
        currentValue: Optional[str] = None,
        entityIds: Optional[list[int]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk delete issue property\n\nHTTP DELETE /rest/api/3/issue/properties/{propertyKey}\nPath params:\n  - propertyKey (str)\nBody (application/json) fields:\n  - currentValue (str, optional)\n  - entityIds (list[int], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if currentValue is not None:
            _body['currentValue'] = currentValue
        if entityIds is not None:
            _body['entityIds'] = entityIds
        rel_path = '/rest/api/3/issue/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def bulk_set_issue_property(
        self,
        propertyKey: str,
        expression: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk set issue property\n\nHTTP PUT /rest/api/3/issue/properties/{propertyKey}\nPath params:\n  - propertyKey (str)\nBody (application/json) fields:\n  - expression (str, optional)\n  - filter (Dict[str, Any], optional)\n  - value (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if expression is not None:
            _body['expression'] = expression
        if filter is not None:
            _body['filter'] = filter
        if value is not None:
            _body['value'] = value
        rel_path = '/rest/api/3/issue/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def unarchive_issues(
        self,
        issueIdsOrKeys: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Unarchive issue(s) by issue keys/ID\n\nHTTP PUT /rest/api/3/issue/unarchive\nBody (application/json) fields:\n  - issueIdsOrKeys (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if issueIdsOrKeys is not None:
            _body['issueIdsOrKeys'] = issueIdsOrKeys
        rel_path = '/rest/api/3/issue/unarchive'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_is_watching_issue_bulk(
        self,
        issueIds: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get is watching issue bulk\n\nHTTP POST /rest/api/3/issue/watching\nBody (application/json) fields:\n  - issueIds (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['issueIds'] = issueIds
        rel_path = '/rest/api/3/issue/watching'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_issue(
        self,
        issueIdOrKey: str,
        deleteSubtasks: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete issue\n\nHTTP DELETE /rest/api/3/issue/{issueIdOrKey}\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - deleteSubtasks (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if deleteSubtasks is not None:
            _query['deleteSubtasks'] = deleteSubtasks
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue(
        self,
        issueIdOrKey: str,
        fields: Optional[list[str]] = None,
        fieldsByKeys: Optional[bool] = None,
        expand: Optional[str] = None,
        properties: Optional[list[str]] = None,
        updateHistory: Optional[bool] = None,
        failFast: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - fields (list[str], optional)\n  - fieldsByKeys (bool, optional)\n  - expand (str, optional)\n  - properties (list[str], optional)\n  - updateHistory (bool, optional)\n  - failFast (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if fields is not None:
            _query['fields'] = fields
        if fieldsByKeys is not None:
            _query['fieldsByKeys'] = fieldsByKeys
        if expand is not None:
            _query['expand'] = expand
        if properties is not None:
            _query['properties'] = properties
        if updateHistory is not None:
            _query['updateHistory'] = updateHistory
        if failFast is not None:
            _query['failFast'] = failFast
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def edit_issue(
        self,
        issueIdOrKey: str,
        notifyUsers: Optional[bool] = None,
        overrideScreenSecurity: Optional[bool] = None,
        overrideEditableFlag: Optional[bool] = None,
        returnIssue: Optional[bool] = None,
        expand: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        historyMetadata: Optional[Dict[str, Any]] = None,
        properties: Optional[list[Dict[str, Any]]] = None,
        transition: Optional[Dict[str, Any]] = None,
        update: Optional[Dict[str, Any]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Edit issue\n\nHTTP PUT /rest/api/3/issue/{issueIdOrKey}\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - notifyUsers (bool, optional)\n  - overrideScreenSecurity (bool, optional)\n  - overrideEditableFlag (bool, optional)\n  - returnIssue (bool, optional)\n  - expand (str, optional)\nBody (application/json) fields:\n  - fields (Dict[str, Any], optional)\n  - historyMetadata (Dict[str, Any], optional)\n  - properties (list[Dict[str, Any]], optional)\n  - transition (Dict[str, Any], optional)\n  - update (Dict[str, Any], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if notifyUsers is not None:
            _query['notifyUsers'] = notifyUsers
        if overrideScreenSecurity is not None:
            _query['overrideScreenSecurity'] = overrideScreenSecurity
        if overrideEditableFlag is not None:
            _query['overrideEditableFlag'] = overrideEditableFlag
        if returnIssue is not None:
            _query['returnIssue'] = returnIssue
        if expand is not None:
            _query['expand'] = expand
        _body: Dict[str, Any] = {}
        if fields is not None:
            _body['fields'] = fields
        if historyMetadata is not None:
            _body['historyMetadata'] = historyMetadata
        if properties is not None:
            _body['properties'] = properties
        if transition is not None:
            _body['transition'] = transition
        if update is not None:
            _body['update'] = update
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issue/{issueIdOrKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def assign_issue(
        self,
        issueIdOrKey: str,
        accountId: Optional[str] = None,
        accountType: Optional[str] = None,
        active: Optional[bool] = None,
        applicationRoles: Optional[Dict[str, Any]] = None,
        avatarUrls: Optional[Dict[str, Any]] = None,
        displayName: Optional[str] = None,
        emailAddress: Optional[str] = None,
        expand: Optional[str] = None,
        groups: Optional[Dict[str, Any]] = None,
        key: Optional[str] = None,
        locale: Optional[str] = None,
        name: Optional[str] = None,
        self_: Optional[str] = None,
        timeZone: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Assign issue\n\nHTTP PUT /rest/api/3/issue/{issueIdOrKey}/assignee\nPath params:\n  - issueIdOrKey (str)\nBody (application/json) fields:\n  - accountId (str, optional)\n  - accountType (str, optional)\n  - active (bool, optional)\n  - applicationRoles (Dict[str, Any], optional)\n  - avatarUrls (Dict[str, Any], optional)\n  - displayName (str, optional)\n  - emailAddress (str, optional)\n  - expand (str, optional)\n  - groups (Dict[str, Any], optional)\n  - key (str, optional)\n  - locale (str, optional)\n  - name (str, optional)\n  - self (str, optional)\n  - timeZone (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if accountId is not None:
            _body['accountId'] = accountId
        if accountType is not None:
            _body['accountType'] = accountType
        if active is not None:
            _body['active'] = active
        if applicationRoles is not None:
            _body['applicationRoles'] = applicationRoles
        if avatarUrls is not None:
            _body['avatarUrls'] = avatarUrls
        if displayName is not None:
            _body['displayName'] = displayName
        if emailAddress is not None:
            _body['emailAddress'] = emailAddress
        if expand is not None:
            _body['expand'] = expand
        if groups is not None:
            _body['groups'] = groups
        if key is not None:
            _body['key'] = key
        if locale is not None:
            _body['locale'] = locale
        if name is not None:
            _body['name'] = name
        if self_ is not None:
            _body['self'] = self_
        if timeZone is not None:
            _body['timeZone'] = timeZone
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/assignee'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_attachment(
        self,
        issueIdOrKey: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add attachment\n\nHTTP POST /rest/api/3/issue/{issueIdOrKey}/attachments\nPath params:\n  - issueIdOrKey (str)\nBody: multipart/form-data (list[Dict[str, Any]])"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'multipart/form-data')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/attachments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_change_logs(
        self,
        issueIdOrKey: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get changelogs\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/changelog\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/changelog'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_change_logs_by_ids(
        self,
        issueIdOrKey: str,
        changelogIds: list[int],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get changelogs by IDs\n\nHTTP POST /rest/api/3/issue/{issueIdOrKey}/changelog/list\nPath params:\n  - issueIdOrKey (str)\nBody (application/json) fields:\n  - changelogIds (list[int], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['changelogIds'] = changelogIds
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/changelog/list'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_comments(
        self,
        issueIdOrKey: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get comments\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/comment\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - orderBy (str, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/comment'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_comment(
        self,
        issueIdOrKey: str,
        expand: Optional[str] = None,
        author: Optional[Dict[str, Any]] = None,
        body_body: Optional[str] = None,
        created: Optional[str] = None,
        id: Optional[str] = None,
        jsdAuthorCanSeeRequest: Optional[bool] = None,
        jsdPublic: Optional[bool] = None,
        properties: Optional[list[Dict[str, Any]]] = None,
        renderedBody: Optional[str] = None,
        self_: Optional[str] = None,
        updateAuthor: Optional[Dict[str, Any]] = None,
        updated: Optional[str] = None,
        visibility: Optional[Dict[str, Any]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add comment\n\nHTTP POST /rest/api/3/issue/{issueIdOrKey}/comment\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - expand (str, optional)\nBody (application/json) fields:\n  - author (Dict[str, Any], optional)\n  - body (str, optional)\n  - created (str, optional)\n  - id (str, optional)\n  - jsdAuthorCanSeeRequest (bool, optional)\n  - jsdPublic (bool, optional)\n  - properties (list[Dict[str, Any]], optional)\n  - renderedBody (str, optional)\n  - self (str, optional)\n  - updateAuthor (Dict[str, Any], optional)\n  - updated (str, optional)\n  - visibility (Dict[str, Any], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body: Dict[str, Any] = {}
        if author is not None:
            _body['author'] = author
        if body_body is not None:
            _body['body'] = body_body
        if created is not None:
            _body['created'] = created
        if id is not None:
            _body['id'] = id
        if jsdAuthorCanSeeRequest is not None:
            _body['jsdAuthorCanSeeRequest'] = jsdAuthorCanSeeRequest
        if jsdPublic is not None:
            _body['jsdPublic'] = jsdPublic
        if properties is not None:
            _body['properties'] = properties
        if renderedBody is not None:
            _body['renderedBody'] = renderedBody
        if self_ is not None:
            _body['self'] = self_
        if updateAuthor is not None:
            _body['updateAuthor'] = updateAuthor
        if updated is not None:
            _body['updated'] = updated
        if visibility is not None:
            _body['visibility'] = visibility
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/comment'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_comment(
        self,
        issueIdOrKey: str,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete comment\n\nHTTP DELETE /rest/api/3/issue/{issueIdOrKey}/comment/{id}\nPath params:\n  - issueIdOrKey (str)\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/comment/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_comment(
        self,
        issueIdOrKey: str,
        id: str,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get comment\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/comment/{id}\nPath params:\n  - issueIdOrKey (str)\n  - id (str)\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/comment/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_comment(
        self,
        issueIdOrKey: str,
        id: str,
        notifyUsers: Optional[bool] = None,
        overrideEditableFlag: Optional[bool] = None,
        expand: Optional[str] = None,
        author: Optional[Dict[str, Any]] = None,
        body_body: Optional[str] = None,
        created: Optional[str] = None,
        id_body: Optional[str] = None,
        jsdAuthorCanSeeRequest: Optional[bool] = None,
        jsdPublic: Optional[bool] = None,
        properties: Optional[list[Dict[str, Any]]] = None,
        renderedBody: Optional[str] = None,
        self_: Optional[str] = None,
        updateAuthor: Optional[Dict[str, Any]] = None,
        updated: Optional[str] = None,
        visibility: Optional[Dict[str, Any]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update comment\n\nHTTP PUT /rest/api/3/issue/{issueIdOrKey}/comment/{id}\nPath params:\n  - issueIdOrKey (str)\n  - id (str)\nQuery params:\n  - notifyUsers (bool, optional)\n  - overrideEditableFlag (bool, optional)\n  - expand (str, optional)\nBody (application/json) fields:\n  - author (Dict[str, Any], optional)\n  - body (str, optional)\n  - created (str, optional)\n  - id (str, optional)\n  - jsdAuthorCanSeeRequest (bool, optional)\n  - jsdPublic (bool, optional)\n  - properties (list[Dict[str, Any]], optional)\n  - renderedBody (str, optional)\n  - self (str, optional)\n  - updateAuthor (Dict[str, Any], optional)\n  - updated (str, optional)\n  - visibility (Dict[str, Any], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if notifyUsers is not None:
            _query['notifyUsers'] = notifyUsers
        if overrideEditableFlag is not None:
            _query['overrideEditableFlag'] = overrideEditableFlag
        if expand is not None:
            _query['expand'] = expand
        _body: Dict[str, Any] = {}
        if author is not None:
            _body['author'] = author
        if body_body is not None:
            _body['body'] = body_body
        if created is not None:
            _body['created'] = created
        if id_body is not None:
            _body['id'] = id_body
        if jsdAuthorCanSeeRequest is not None:
            _body['jsdAuthorCanSeeRequest'] = jsdAuthorCanSeeRequest
        if jsdPublic is not None:
            _body['jsdPublic'] = jsdPublic
        if properties is not None:
            _body['properties'] = properties
        if renderedBody is not None:
            _body['renderedBody'] = renderedBody
        if self_ is not None:
            _body['self'] = self_
        if updateAuthor is not None:
            _body['updateAuthor'] = updateAuthor
        if updated is not None:
            _body['updated'] = updated
        if visibility is not None:
            _body['visibility'] = visibility
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/comment/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_edit_issue_meta(
        self,
        issueIdOrKey: str,
        overrideScreenSecurity: Optional[bool] = None,
        overrideEditableFlag: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get edit issue metadata\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/editmeta\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - overrideScreenSecurity (bool, optional)\n  - overrideEditableFlag (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if overrideScreenSecurity is not None:
            _query['overrideScreenSecurity'] = overrideScreenSecurity
        if overrideEditableFlag is not None:
            _query['overrideEditableFlag'] = overrideEditableFlag
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/editmeta'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def notify(
        self,
        issueIdOrKey: str,
        htmlBody: Optional[str] = None,
        restrict: Optional[Dict[str, Any]] = None,
        subject: Optional[str] = None,
        textBody: Optional[str] = None,
        to: Optional[Dict[str, Any]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Send notification for issue\n\nHTTP POST /rest/api/3/issue/{issueIdOrKey}/notify\nPath params:\n  - issueIdOrKey (str)\nBody (application/json) fields:\n  - htmlBody (str, optional)\n  - restrict (Dict[str, Any], optional)\n  - subject (str, optional)\n  - textBody (str, optional)\n  - to (Dict[str, Any], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if htmlBody is not None:
            _body['htmlBody'] = htmlBody
        if restrict is not None:
            _body['restrict'] = restrict
        if subject is not None:
            _body['subject'] = subject
        if textBody is not None:
            _body['textBody'] = textBody
        if to is not None:
            _body['to'] = to
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/notify'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_property_keys(
        self,
        issueIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue property keys\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/properties\nPath params:\n  - issueIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_issue_property(
        self,
        issueIdOrKey: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete issue property\n\nHTTP DELETE /rest/api/3/issue/{issueIdOrKey}/properties/{propertyKey}\nPath params:\n  - issueIdOrKey (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_property(
        self,
        issueIdOrKey: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue property\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/properties/{propertyKey}\nPath params:\n  - issueIdOrKey (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_issue_property(
        self,
        issueIdOrKey: str,
        propertyKey: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set issue property\n\nHTTP PUT /rest/api/3/issue/{issueIdOrKey}/properties/{propertyKey}\nPath params:\n  - issueIdOrKey (str)\n  - propertyKey (str)\nBody: application/json (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_remote_issue_link_by_global_id(
        self,
        issueIdOrKey: str,
        globalId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete remote issue link by global ID\n\nHTTP DELETE /rest/api/3/issue/{issueIdOrKey}/remotelink\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - globalId (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _query['globalId'] = globalId
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/remotelink'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_remote_issue_links(
        self,
        issueIdOrKey: str,
        globalId: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get remote issue links\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/remotelink\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - globalId (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if globalId is not None:
            _query['globalId'] = globalId
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/remotelink'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_or_update_remote_issue_link(
        self,
        issueIdOrKey: str,
        object: Dict[str, Any],
        application: Optional[Dict[str, Any]] = None,
        globalId: Optional[str] = None,
        relationship: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create or update remote issue link\n\nHTTP POST /rest/api/3/issue/{issueIdOrKey}/remotelink\nPath params:\n  - issueIdOrKey (str)\nBody (application/json) fields:\n  - application (Dict[str, Any], optional)\n  - globalId (str, optional)\n  - object (Dict[str, Any], required)\n  - relationship (str, optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if application is not None:
            _body['application'] = application
        if globalId is not None:
            _body['globalId'] = globalId
        _body['object'] = object
        if relationship is not None:
            _body['relationship'] = relationship
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/remotelink'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_remote_issue_link_by_id(
        self,
        issueIdOrKey: str,
        linkId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete remote issue link by ID\n\nHTTP DELETE /rest/api/3/issue/{issueIdOrKey}/remotelink/{linkId}\nPath params:\n  - issueIdOrKey (str)\n  - linkId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'linkId': linkId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/remotelink/{linkId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_remote_issue_link_by_id(
        self,
        issueIdOrKey: str,
        linkId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get remote issue link by ID\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/remotelink/{linkId}\nPath params:\n  - issueIdOrKey (str)\n  - linkId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'linkId': linkId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/remotelink/{linkId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_remote_issue_link(
        self,
        issueIdOrKey: str,
        linkId: str,
        object: Dict[str, Any],
        application: Optional[Dict[str, Any]] = None,
        globalId: Optional[str] = None,
        relationship: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update remote issue link by ID\n\nHTTP PUT /rest/api/3/issue/{issueIdOrKey}/remotelink/{linkId}\nPath params:\n  - issueIdOrKey (str)\n  - linkId (str)\nBody (application/json) fields:\n  - application (Dict[str, Any], optional)\n  - globalId (str, optional)\n  - object (Dict[str, Any], required)\n  - relationship (str, optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'linkId': linkId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if application is not None:
            _body['application'] = application
        if globalId is not None:
            _body['globalId'] = globalId
        _body['object'] = object
        if relationship is not None:
            _body['relationship'] = relationship
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/remotelink/{linkId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_transitions(
        self,
        issueIdOrKey: str,
        expand: Optional[str] = None,
        transitionId: Optional[str] = None,
        skipRemoteOnlyCondition: Optional[bool] = None,
        includeUnavailableTransitions: Optional[bool] = None,
        sortByOpsBarAndStatus: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get transitions\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/transitions\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - expand (str, optional)\n  - transitionId (str, optional)\n  - skipRemoteOnlyCondition (bool, optional)\n  - includeUnavailableTransitions (bool, optional)\n  - sortByOpsBarAndStatus (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        if transitionId is not None:
            _query['transitionId'] = transitionId
        if skipRemoteOnlyCondition is not None:
            _query['skipRemoteOnlyCondition'] = skipRemoteOnlyCondition
        if includeUnavailableTransitions is not None:
            _query['includeUnavailableTransitions'] = includeUnavailableTransitions
        if sortByOpsBarAndStatus is not None:
            _query['sortByOpsBarAndStatus'] = sortByOpsBarAndStatus
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/transitions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def do_transition(
        self,
        issueIdOrKey: str,
        fields: Optional[Dict[str, Any]] = None,
        historyMetadata: Optional[Dict[str, Any]] = None,
        properties: Optional[list[Dict[str, Any]]] = None,
        transition: Optional[Dict[str, Any]] = None,
        update: Optional[Dict[str, Any]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Transition issue\n\nHTTP POST /rest/api/3/issue/{issueIdOrKey}/transitions\nPath params:\n  - issueIdOrKey (str)\nBody (application/json) fields:\n  - fields (Dict[str, Any], optional)\n  - historyMetadata (Dict[str, Any], optional)\n  - properties (list[Dict[str, Any]], optional)\n  - transition (Dict[str, Any], optional)\n  - update (Dict[str, Any], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if fields is not None:
            _body['fields'] = fields
        if historyMetadata is not None:
            _body['historyMetadata'] = historyMetadata
        if properties is not None:
            _body['properties'] = properties
        if transition is not None:
            _body['transition'] = transition
        if update is not None:
            _body['update'] = update
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/transitions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_vote(
        self,
        issueIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete vote\n\nHTTP DELETE /rest/api/3/issue/{issueIdOrKey}/votes\nPath params:\n  - issueIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/votes'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_votes(
        self,
        issueIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get votes\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/votes\nPath params:\n  - issueIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/votes'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_vote(
        self,
        issueIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add vote\n\nHTTP POST /rest/api/3/issue/{issueIdOrKey}/votes\nPath params:\n  - issueIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/votes'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_watcher(
        self,
        issueIdOrKey: str,
        username: Optional[str] = None,
        accountId: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete watcher\n\nHTTP DELETE /rest/api/3/issue/{issueIdOrKey}/watchers\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - username (str, optional)\n  - accountId (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if username is not None:
            _query['username'] = username
        if accountId is not None:
            _query['accountId'] = accountId
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/watchers'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_watchers(
        self,
        issueIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue watchers\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/watchers\nPath params:\n  - issueIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/watchers'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_watcher(
        self,
        issueIdOrKey: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add watcher\n\nHTTP POST /rest/api/3/issue/{issueIdOrKey}/watchers\nPath params:\n  - issueIdOrKey (str)\nBody: application/json (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/watchers'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def bulk_delete_worklogs(
        self,
        issueIdOrKey: str,
        ids: list[int],
        adjustEstimate: Optional[str] = None,
        overrideEditableFlag: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk delete worklogs\n\nHTTP DELETE /rest/api/3/issue/{issueIdOrKey}/worklog\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - adjustEstimate (str, optional)\n  - overrideEditableFlag (bool, optional)\nBody (application/json) fields:\n  - ids (list[int], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if adjustEstimate is not None:
            _query['adjustEstimate'] = adjustEstimate
        if overrideEditableFlag is not None:
            _query['overrideEditableFlag'] = overrideEditableFlag
        _body: Dict[str, Any] = {}
        _body['ids'] = ids
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/worklog'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_worklog(
        self,
        issueIdOrKey: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        startedAfter: Optional[int] = None,
        startedBefore: Optional[int] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue worklogs\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/worklog\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - startedAfter (int, optional)\n  - startedBefore (int, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if startedAfter is not None:
            _query['startedAfter'] = startedAfter
        if startedBefore is not None:
            _query['startedBefore'] = startedBefore
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/worklog'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_worklog(
        self,
        issueIdOrKey: str,
        notifyUsers: Optional[bool] = None,
        adjustEstimate: Optional[str] = None,
        newEstimate: Optional[str] = None,
        reduceBy: Optional[str] = None,
        expand: Optional[str] = None,
        overrideEditableFlag: Optional[bool] = None,
        author: Optional[Dict[str, Any]] = None,
        comment: Optional[str] = None,
        created: Optional[str] = None,
        id: Optional[str] = None,
        issueId: Optional[str] = None,
        properties: Optional[list[Dict[str, Any]]] = None,
        self_: Optional[str] = None,
        started: Optional[str] = None,
        timeSpent: Optional[str] = None,
        timeSpentSeconds: Optional[int] = None,
        updateAuthor: Optional[Dict[str, Any]] = None,
        updated: Optional[str] = None,
        visibility: Optional[Dict[str, Any]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add worklog\n\nHTTP POST /rest/api/3/issue/{issueIdOrKey}/worklog\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - notifyUsers (bool, optional)\n  - adjustEstimate (str, optional)\n  - newEstimate (str, optional)\n  - reduceBy (str, optional)\n  - expand (str, optional)\n  - overrideEditableFlag (bool, optional)\nBody (application/json) fields:\n  - author (Dict[str, Any], optional)\n  - comment (str, optional)\n  - created (str, optional)\n  - id (str, optional)\n  - issueId (str, optional)\n  - properties (list[Dict[str, Any]], optional)\n  - self (str, optional)\n  - started (str, optional)\n  - timeSpent (str, optional)\n  - timeSpentSeconds (int, optional)\n  - updateAuthor (Dict[str, Any], optional)\n  - updated (str, optional)\n  - visibility (Dict[str, Any], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if notifyUsers is not None:
            _query['notifyUsers'] = notifyUsers
        if adjustEstimate is not None:
            _query['adjustEstimate'] = adjustEstimate
        if newEstimate is not None:
            _query['newEstimate'] = newEstimate
        if reduceBy is not None:
            _query['reduceBy'] = reduceBy
        if expand is not None:
            _query['expand'] = expand
        if overrideEditableFlag is not None:
            _query['overrideEditableFlag'] = overrideEditableFlag
        _body: Dict[str, Any] = {}
        if author is not None:
            _body['author'] = author
        if comment is not None:
            _body['comment'] = comment
        if created is not None:
            _body['created'] = created
        if id is not None:
            _body['id'] = id
        if issueId is not None:
            _body['issueId'] = issueId
        if properties is not None:
            _body['properties'] = properties
        if self_ is not None:
            _body['self'] = self_
        if started is not None:
            _body['started'] = started
        if timeSpent is not None:
            _body['timeSpent'] = timeSpent
        if timeSpentSeconds is not None:
            _body['timeSpentSeconds'] = timeSpentSeconds
        if updateAuthor is not None:
            _body['updateAuthor'] = updateAuthor
        if updated is not None:
            _body['updated'] = updated
        if visibility is not None:
            _body['visibility'] = visibility
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/worklog'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def bulk_move_worklogs(
        self,
        issueIdOrKey: str,
        adjustEstimate: Optional[str] = None,
        overrideEditableFlag: Optional[bool] = None,
        ids: Optional[list[int]] = None,
        issueIdOrKey_body: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk move worklogs\n\nHTTP POST /rest/api/3/issue/{issueIdOrKey}/worklog/move\nPath params:\n  - issueIdOrKey (str)\nQuery params:\n  - adjustEstimate (str, optional)\n  - overrideEditableFlag (bool, optional)\nBody (application/json) fields:\n  - ids (list[int], optional)\n  - issueIdOrKey (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if adjustEstimate is not None:
            _query['adjustEstimate'] = adjustEstimate
        if overrideEditableFlag is not None:
            _query['overrideEditableFlag'] = overrideEditableFlag
        _body: Dict[str, Any] = {}
        if ids is not None:
            _body['ids'] = ids
        if issueIdOrKey_body is not None:
            _body['issueIdOrKey'] = issueIdOrKey_body
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/worklog/move'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_worklog(
        self,
        issueIdOrKey: str,
        id: str,
        notifyUsers: Optional[bool] = None,
        adjustEstimate: Optional[str] = None,
        newEstimate: Optional[str] = None,
        increaseBy: Optional[str] = None,
        overrideEditableFlag: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete worklog\n\nHTTP DELETE /rest/api/3/issue/{issueIdOrKey}/worklog/{id}\nPath params:\n  - issueIdOrKey (str)\n  - id (str)\nQuery params:\n  - notifyUsers (bool, optional)\n  - adjustEstimate (str, optional)\n  - newEstimate (str, optional)\n  - increaseBy (str, optional)\n  - overrideEditableFlag (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if notifyUsers is not None:
            _query['notifyUsers'] = notifyUsers
        if adjustEstimate is not None:
            _query['adjustEstimate'] = adjustEstimate
        if newEstimate is not None:
            _query['newEstimate'] = newEstimate
        if increaseBy is not None:
            _query['increaseBy'] = increaseBy
        if overrideEditableFlag is not None:
            _query['overrideEditableFlag'] = overrideEditableFlag
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/worklog/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_worklog(
        self,
        issueIdOrKey: str,
        id: str,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get worklog\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/worklog/{id}\nPath params:\n  - issueIdOrKey (str)\n  - id (str)\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/worklog/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_worklog(
        self,
        issueIdOrKey: str,
        id: str,
        notifyUsers: Optional[bool] = None,
        adjustEstimate: Optional[str] = None,
        newEstimate: Optional[str] = None,
        expand: Optional[str] = None,
        overrideEditableFlag: Optional[bool] = None,
        author: Optional[Dict[str, Any]] = None,
        comment: Optional[str] = None,
        created: Optional[str] = None,
        id_body: Optional[str] = None,
        issueId: Optional[str] = None,
        properties: Optional[list[Dict[str, Any]]] = None,
        self_: Optional[str] = None,
        started: Optional[str] = None,
        timeSpent: Optional[str] = None,
        timeSpentSeconds: Optional[int] = None,
        updateAuthor: Optional[Dict[str, Any]] = None,
        updated: Optional[str] = None,
        visibility: Optional[Dict[str, Any]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update worklog\n\nHTTP PUT /rest/api/3/issue/{issueIdOrKey}/worklog/{id}\nPath params:\n  - issueIdOrKey (str)\n  - id (str)\nQuery params:\n  - notifyUsers (bool, optional)\n  - adjustEstimate (str, optional)\n  - newEstimate (str, optional)\n  - expand (str, optional)\n  - overrideEditableFlag (bool, optional)\nBody (application/json) fields:\n  - author (Dict[str, Any], optional)\n  - comment (str, optional)\n  - created (str, optional)\n  - id (str, optional)\n  - issueId (str, optional)\n  - properties (list[Dict[str, Any]], optional)\n  - self (str, optional)\n  - started (str, optional)\n  - timeSpent (str, optional)\n  - timeSpentSeconds (int, optional)\n  - updateAuthor (Dict[str, Any], optional)\n  - updated (str, optional)\n  - visibility (Dict[str, Any], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if notifyUsers is not None:
            _query['notifyUsers'] = notifyUsers
        if adjustEstimate is not None:
            _query['adjustEstimate'] = adjustEstimate
        if newEstimate is not None:
            _query['newEstimate'] = newEstimate
        if expand is not None:
            _query['expand'] = expand
        if overrideEditableFlag is not None:
            _query['overrideEditableFlag'] = overrideEditableFlag
        _body: Dict[str, Any] = {}
        if author is not None:
            _body['author'] = author
        if comment is not None:
            _body['comment'] = comment
        if created is not None:
            _body['created'] = created
        if id_body is not None:
            _body['id'] = id_body
        if issueId is not None:
            _body['issueId'] = issueId
        if properties is not None:
            _body['properties'] = properties
        if self_ is not None:
            _body['self'] = self_
        if started is not None:
            _body['started'] = started
        if timeSpent is not None:
            _body['timeSpent'] = timeSpent
        if timeSpentSeconds is not None:
            _body['timeSpentSeconds'] = timeSpentSeconds
        if updateAuthor is not None:
            _body['updateAuthor'] = updateAuthor
        if updated is not None:
            _body['updated'] = updated
        if visibility is not None:
            _body['visibility'] = visibility
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/worklog/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_worklog_property_keys(
        self,
        issueIdOrKey: str,
        worklogId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get worklog property keys\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/worklog/{worklogId}/properties\nPath params:\n  - issueIdOrKey (str)\n  - worklogId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'worklogId': worklogId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/worklog/{worklogId}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_worklog_property(
        self,
        issueIdOrKey: str,
        worklogId: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete worklog property\n\nHTTP DELETE /rest/api/3/issue/{issueIdOrKey}/worklog/{worklogId}/properties/{propertyKey}\nPath params:\n  - issueIdOrKey (str)\n  - worklogId (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'worklogId': worklogId,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/worklog/{worklogId}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_worklog_property(
        self,
        issueIdOrKey: str,
        worklogId: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get worklog property\n\nHTTP GET /rest/api/3/issue/{issueIdOrKey}/worklog/{worklogId}/properties/{propertyKey}\nPath params:\n  - issueIdOrKey (str)\n  - worklogId (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'worklogId': worklogId,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/worklog/{worklogId}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_worklog_property(
        self,
        issueIdOrKey: str,
        worklogId: str,
        propertyKey: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set worklog property\n\nHTTP PUT /rest/api/3/issue/{issueIdOrKey}/worklog/{worklogId}/properties/{propertyKey}\nPath params:\n  - issueIdOrKey (str)\n  - worklogId (str)\n  - propertyKey (str)\nBody: application/json (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueIdOrKey': issueIdOrKey,
            'worklogId': worklogId,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/api/3/issue/{issueIdOrKey}/worklog/{worklogId}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def link_issues(
        self,
        inwardIssue: Dict[str, Any],
        outwardIssue: Dict[str, Any],
        type: Dict[str, Any],
        comment: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create issue link\n\nHTTP POST /rest/api/3/issueLink\nBody (application/json) fields:\n  - comment (Dict[str, Any], optional)\n  - inwardIssue (Dict[str, Any], required)\n  - outwardIssue (Dict[str, Any], required)\n  - type (Dict[str, Any], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if comment is not None:
            _body['comment'] = comment
        _body['inwardIssue'] = inwardIssue
        _body['outwardIssue'] = outwardIssue
        _body['type'] = type
        rel_path = '/rest/api/3/issueLink'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_issue_link(
        self,
        linkId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete issue link\n\nHTTP DELETE /rest/api/3/issueLink/{linkId}\nPath params:\n  - linkId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'linkId': linkId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issueLink/{linkId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_link(
        self,
        linkId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue link\n\nHTTP GET /rest/api/3/issueLink/{linkId}\nPath params:\n  - linkId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'linkId': linkId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issueLink/{linkId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_link_types(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue link types\n\nHTTP GET /rest/api/3/issueLinkType"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issueLinkType'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_issue_link_type(
        self,
        id: Optional[str] = None,
        inward: Optional[str] = None,
        name: Optional[str] = None,
        outward: Optional[str] = None,
        self_: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create issue link type\n\nHTTP POST /rest/api/3/issueLinkType\nBody (application/json) fields:\n  - id (str, optional)\n  - inward (str, optional)\n  - name (str, optional)\n  - outward (str, optional)\n  - self (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if id is not None:
            _body['id'] = id
        if inward is not None:
            _body['inward'] = inward
        if name is not None:
            _body['name'] = name
        if outward is not None:
            _body['outward'] = outward
        if self_ is not None:
            _body['self'] = self_
        rel_path = '/rest/api/3/issueLinkType'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_issue_link_type(
        self,
        issueLinkTypeId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete issue link type\n\nHTTP DELETE /rest/api/3/issueLinkType/{issueLinkTypeId}\nPath params:\n  - issueLinkTypeId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueLinkTypeId': issueLinkTypeId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issueLinkType/{issueLinkTypeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_link_type(
        self,
        issueLinkTypeId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue link type\n\nHTTP GET /rest/api/3/issueLinkType/{issueLinkTypeId}\nPath params:\n  - issueLinkTypeId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueLinkTypeId': issueLinkTypeId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issueLinkType/{issueLinkTypeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_issue_link_type(
        self,
        issueLinkTypeId: str,
        id: Optional[str] = None,
        inward: Optional[str] = None,
        name: Optional[str] = None,
        outward: Optional[str] = None,
        self_: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update issue link type\n\nHTTP PUT /rest/api/3/issueLinkType/{issueLinkTypeId}\nPath params:\n  - issueLinkTypeId (str)\nBody (application/json) fields:\n  - id (str, optional)\n  - inward (str, optional)\n  - name (str, optional)\n  - outward (str, optional)\n  - self (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueLinkTypeId': issueLinkTypeId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if id is not None:
            _body['id'] = id
        if inward is not None:
            _body['inward'] = inward
        if name is not None:
            _body['name'] = name
        if outward is not None:
            _body['outward'] = outward
        if self_ is not None:
            _body['self'] = self_
        rel_path = '/rest/api/3/issueLinkType/{issueLinkTypeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def export_archived_issues(
        self,
        archivedBy: Optional[list[str]] = None,
        archivedDateRange: Optional[Dict[str, Any]] = None,
        issueTypes: Optional[list[str]] = None,
        projects: Optional[list[str]] = None,
        reporters: Optional[list[str]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Export archived issue(s)\n\nHTTP PUT /rest/api/3/issues/archive/export\nBody (application/json) fields:\n  - archivedBy (list[str], optional)\n  - archivedDateRange (Dict[str, Any], optional)\n  - issueTypes (list[str], optional)\n  - projects (list[str], optional)\n  - reporters (list[str], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if archivedBy is not None:
            _body['archivedBy'] = archivedBy
        if archivedDateRange is not None:
            _body['archivedDateRange'] = archivedDateRange
        if issueTypes is not None:
            _body['issueTypes'] = issueTypes
        if projects is not None:
            _body['projects'] = projects
        if reporters is not None:
            _body['reporters'] = reporters
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issues/archive/export'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_security_schemes(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue security schemes\n\nHTTP GET /rest/api/3/issuesecurityschemes"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuesecurityschemes'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_issue_security_scheme(
        self,
        name: str,
        description: Optional[str] = None,
        levels: Optional[list[Dict[str, Any]]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create issue security scheme\n\nHTTP POST /rest/api/3/issuesecurityschemes\nBody (application/json) fields:\n  - description (str, optional)\n  - levels (list[Dict[str, Any]], optional)\n  - name (str, required)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if levels is not None:
            _body['levels'] = levels
        _body['name'] = name
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issuesecurityschemes'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_security_levels(
        self,
        startAt: Optional[str] = None,
        maxResults: Optional[str] = None,
        id: Optional[list[str]] = None,
        schemeId: Optional[list[str]] = None,
        onlyDefault: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue security levels\n\nHTTP GET /rest/api/3/issuesecurityschemes/level\nQuery params:\n  - startAt (str, optional)\n  - maxResults (str, optional)\n  - id (list[str], optional)\n  - schemeId (list[str], optional)\n  - onlyDefault (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        if schemeId is not None:
            _query['schemeId'] = schemeId
        if onlyDefault is not None:
            _query['onlyDefault'] = onlyDefault
        _body = None
        rel_path = '/rest/api/3/issuesecurityschemes/level'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_default_levels(
        self,
        defaultValues: list[Dict[str, Any]],
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set default issue security levels\n\nHTTP PUT /rest/api/3/issuesecurityschemes/level/default\nBody (application/json) fields:\n  - defaultValues (list[Dict[str, Any]], required)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['defaultValues'] = defaultValues
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issuesecurityschemes/level/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_security_level_members(
        self,
        startAt: Optional[str] = None,
        maxResults: Optional[str] = None,
        id: Optional[list[str]] = None,
        schemeId: Optional[list[str]] = None,
        levelId: Optional[list[str]] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue security level members\n\nHTTP GET /rest/api/3/issuesecurityschemes/level/member\nQuery params:\n  - startAt (str, optional)\n  - maxResults (str, optional)\n  - id (list[str], optional)\n  - schemeId (list[str], optional)\n  - levelId (list[str], optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        if schemeId is not None:
            _query['schemeId'] = schemeId
        if levelId is not None:
            _query['levelId'] = levelId
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/issuesecurityschemes/level/member'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def search_projects_using_security_schemes(
        self,
        startAt: Optional[str] = None,
        maxResults: Optional[str] = None,
        issueSecuritySchemeId: Optional[list[str]] = None,
        projectId: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get projects using issue security schemes\n\nHTTP GET /rest/api/3/issuesecurityschemes/project\nQuery params:\n  - startAt (str, optional)\n  - maxResults (str, optional)\n  - issueSecuritySchemeId (list[str], optional)\n  - projectId (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if issueSecuritySchemeId is not None:
            _query['issueSecuritySchemeId'] = issueSecuritySchemeId
        if projectId is not None:
            _query['projectId'] = projectId
        _body = None
        rel_path = '/rest/api/3/issuesecurityschemes/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def associate_schemes_to_projects(
        self,
        projectId: str,
        schemeId: str,
        oldToNewSecurityLevelMappings: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Associate security scheme to project\n\nHTTP PUT /rest/api/3/issuesecurityschemes/project\nBody (application/json) fields:\n  - oldToNewSecurityLevelMappings (list[Dict[str, Any]], optional)\n  - projectId (str, required)\n  - schemeId (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if oldToNewSecurityLevelMappings is not None:
            _body['oldToNewSecurityLevelMappings'] = oldToNewSecurityLevelMappings
        _body['projectId'] = projectId
        _body['schemeId'] = schemeId
        rel_path = '/rest/api/3/issuesecurityschemes/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def search_security_schemes(
        self,
        startAt: Optional[str] = None,
        maxResults: Optional[str] = None,
        id: Optional[list[str]] = None,
        projectId: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Search issue security schemes\n\nHTTP GET /rest/api/3/issuesecurityschemes/search\nQuery params:\n  - startAt (str, optional)\n  - maxResults (str, optional)\n  - id (list[str], optional)\n  - projectId (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        if projectId is not None:
            _query['projectId'] = projectId
        _body = None
        rel_path = '/rest/api/3/issuesecurityschemes/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_security_scheme(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue security scheme\n\nHTTP GET /rest/api/3/issuesecurityschemes/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuesecurityschemes/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_issue_security_scheme(
        self,
        id: str,
        description: Optional[str] = None,
        name: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update issue security scheme\n\nHTTP PUT /rest/api/3/issuesecurityschemes/{id}\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        rel_path = '/rest/api/3/issuesecurityschemes/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_security_level_members(
        self,
        issueSecuritySchemeId: int,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        issueSecurityLevelId: Optional[list[str]] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue security level members by issue security scheme\n\nHTTP GET /rest/api/3/issuesecurityschemes/{issueSecuritySchemeId}/members\nPath params:\n  - issueSecuritySchemeId (int)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - issueSecurityLevelId (list[str], optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueSecuritySchemeId': issueSecuritySchemeId,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if issueSecurityLevelId is not None:
            _query['issueSecurityLevelId'] = issueSecurityLevelId
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/issuesecurityschemes/{issueSecuritySchemeId}/members'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_security_scheme(
        self,
        schemeId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete issue security scheme\n\nHTTP DELETE /rest/api/3/issuesecurityschemes/{schemeId}\nPath params:\n  - schemeId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuesecurityschemes/{schemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_security_level(
        self,
        schemeId: str,
        levels: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add issue security levels\n\nHTTP PUT /rest/api/3/issuesecurityschemes/{schemeId}/level\nPath params:\n  - schemeId (str)\nBody (application/json) fields:\n  - levels (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if levels is not None:
            _body['levels'] = levels
        rel_path = '/rest/api/3/issuesecurityschemes/{schemeId}/level'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_level(
        self,
        schemeId: str,
        levelId: str,
        replaceWith: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove issue security level\n\nHTTP DELETE /rest/api/3/issuesecurityschemes/{schemeId}/level/{levelId}\nPath params:\n  - schemeId (str)\n  - levelId (str)\nQuery params:\n  - replaceWith (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
            'levelId': levelId,
        }
        _query: Dict[str, Any] = {}
        if replaceWith is not None:
            _query['replaceWith'] = replaceWith
        _body = None
        rel_path = '/rest/api/3/issuesecurityschemes/{schemeId}/level/{levelId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_security_level(
        self,
        schemeId: str,
        levelId: str,
        description: Optional[str] = None,
        name: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update issue security level\n\nHTTP PUT /rest/api/3/issuesecurityschemes/{schemeId}/level/{levelId}\nPath params:\n  - schemeId (str)\n  - levelId (str)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
            'levelId': levelId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/issuesecurityschemes/{schemeId}/level/{levelId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_security_level_members(
        self,
        schemeId: str,
        levelId: str,
        members: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add issue security level members\n\nHTTP PUT /rest/api/3/issuesecurityschemes/{schemeId}/level/{levelId}/member\nPath params:\n  - schemeId (str)\n  - levelId (str)\nBody (application/json) fields:\n  - members (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
            'levelId': levelId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if members is not None:
            _body['members'] = members
        rel_path = '/rest/api/3/issuesecurityschemes/{schemeId}/level/{levelId}/member'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_member_from_security_level(
        self,
        schemeId: str,
        levelId: str,
        memberId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove member from issue security level\n\nHTTP DELETE /rest/api/3/issuesecurityschemes/{schemeId}/level/{levelId}/member/{memberId}\nPath params:\n  - schemeId (str)\n  - levelId (str)\n  - memberId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
            'levelId': levelId,
            'memberId': memberId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuesecurityschemes/{schemeId}/level/{levelId}/member/{memberId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_all_types(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all issue types for user\n\nHTTP GET /rest/api/3/issuetype"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuetype'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_issue_type(
        self,
        name: str,
        description: Optional[str] = None,
        hierarchyLevel: Optional[int] = None,
        type: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create issue type\n\nHTTP POST /rest/api/3/issuetype\nBody (application/json) fields:\n  - description (str, optional)\n  - hierarchyLevel (int, optional)\n  - name (str, required)\n  - type (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if hierarchyLevel is not None:
            _body['hierarchyLevel'] = hierarchyLevel
        _body['name'] = name
        if type is not None:
            _body['type'] = type
        rel_path = '/rest/api/3/issuetype'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_types_for_project(
        self,
        projectId: int,
        level: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue types for project\n\nHTTP GET /rest/api/3/issuetype/project\nQuery params:\n  - projectId (int, required)\n  - level (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['projectId'] = projectId
        if level is not None:
            _query['level'] = level
        _body = None
        rel_path = '/rest/api/3/issuetype/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_issue_type(
        self,
        id: str,
        alternativeIssueTypeId: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete issue type\n\nHTTP DELETE /rest/api/3/issuetype/{id}\nPath params:\n  - id (str)\nQuery params:\n  - alternativeIssueTypeId (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if alternativeIssueTypeId is not None:
            _query['alternativeIssueTypeId'] = alternativeIssueTypeId
        _body = None
        rel_path = '/rest/api/3/issuetype/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_type(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue type\n\nHTTP GET /rest/api/3/issuetype/{id}\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuetype/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_issue_type(
        self,
        id: str,
        avatarId: Optional[int] = None,
        description: Optional[str] = None,
        name: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update issue type\n\nHTTP PUT /rest/api/3/issuetype/{id}\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - avatarId (int, optional)\n  - description (str, optional)\n  - name (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if avatarId is not None:
            _body['avatarId'] = avatarId
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        rel_path = '/rest/api/3/issuetype/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_alternative_issue_types(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get alternative issue types\n\nHTTP GET /rest/api/3/issuetype/{id}/alternatives\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuetype/{id}/alternatives'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_issue_type_avatar(
        self,
        id: str,
        size: int,
        x: Optional[int] = None,
        y: Optional[int] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Load issue type avatar\n\nHTTP POST /rest/api/3/issuetype/{id}/avatar2\nPath params:\n  - id (str)\nQuery params:\n  - x (int, optional)\n  - y (int, optional)\n  - size (int, required)\nBody: */* (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', '*/*')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if x is not None:
            _query['x'] = x
        if y is not None:
            _query['y'] = y
        _query['size'] = size
        _body = body
        rel_path = '/rest/api/3/issuetype/{id}/avatar2'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_type_property_keys(
        self,
        issueTypeId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue type property keys\n\nHTTP GET /rest/api/3/issuetype/{issueTypeId}/properties\nPath params:\n  - issueTypeId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueTypeId': issueTypeId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuetype/{issueTypeId}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_issue_type_property(
        self,
        issueTypeId: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete issue type property\n\nHTTP DELETE /rest/api/3/issuetype/{issueTypeId}/properties/{propertyKey}\nPath params:\n  - issueTypeId (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueTypeId': issueTypeId,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuetype/{issueTypeId}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_type_property(
        self,
        issueTypeId: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue type property\n\nHTTP GET /rest/api/3/issuetype/{issueTypeId}/properties/{propertyKey}\nPath params:\n  - issueTypeId (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueTypeId': issueTypeId,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuetype/{issueTypeId}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_issue_type_property(
        self,
        issueTypeId: str,
        propertyKey: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set issue type property\n\nHTTP PUT /rest/api/3/issuetype/{issueTypeId}/properties/{propertyKey}\nPath params:\n  - issueTypeId (str)\n  - propertyKey (str)\nBody: application/json (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueTypeId': issueTypeId,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/api/3/issuetype/{issueTypeId}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_issue_type_schemes(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        id: Optional[list[int]] = None,
        orderBy: Optional[str] = None,
        expand: Optional[str] = None,
        queryString: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all issue type schemes\n\nHTTP GET /rest/api/3/issuetypescheme\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - id (list[int], optional)\n  - orderBy (str, optional)\n  - expand (str, optional)\n  - queryString (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if expand is not None:
            _query['expand'] = expand
        if queryString is not None:
            _query['queryString'] = queryString
        _body = None
        rel_path = '/rest/api/3/issuetypescheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_issue_type_scheme(
        self,
        issueTypeIds: list[str],
        name: str,
        defaultIssueTypeId: Optional[str] = None,
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create issue type scheme\n\nHTTP POST /rest/api/3/issuetypescheme\nBody (application/json) fields:\n  - defaultIssueTypeId (str, optional)\n  - description (str, optional)\n  - issueTypeIds (list[str], required)\n  - name (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if defaultIssueTypeId is not None:
            _body['defaultIssueTypeId'] = defaultIssueTypeId
        if description is not None:
            _body['description'] = description
        _body['issueTypeIds'] = issueTypeIds
        _body['name'] = name
        rel_path = '/rest/api/3/issuetypescheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_type_schemes_mapping(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        issueTypeSchemeId: Optional[list[int]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue type scheme items\n\nHTTP GET /rest/api/3/issuetypescheme/mapping\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - issueTypeSchemeId (list[int], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if issueTypeSchemeId is not None:
            _query['issueTypeSchemeId'] = issueTypeSchemeId
        _body = None
        rel_path = '/rest/api/3/issuetypescheme/mapping'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_type_scheme_for_projects(
        self,
        projectId: list[int],
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue type schemes for projects\n\nHTTP GET /rest/api/3/issuetypescheme/project\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - projectId (list[int], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _query['projectId'] = projectId
        _body = None
        rel_path = '/rest/api/3/issuetypescheme/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def assign_issue_type_scheme_to_project(
        self,
        issueTypeSchemeId: str,
        projectId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Assign issue type scheme to project\n\nHTTP PUT /rest/api/3/issuetypescheme/project\nBody (application/json) fields:\n  - issueTypeSchemeId (str, required)\n  - projectId (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['issueTypeSchemeId'] = issueTypeSchemeId
        _body['projectId'] = projectId
        rel_path = '/rest/api/3/issuetypescheme/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_issue_type_scheme(
        self,
        issueTypeSchemeId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete issue type scheme\n\nHTTP DELETE /rest/api/3/issuetypescheme/{issueTypeSchemeId}\nPath params:\n  - issueTypeSchemeId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueTypeSchemeId': issueTypeSchemeId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuetypescheme/{issueTypeSchemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_issue_type_scheme(
        self,
        issueTypeSchemeId: int,
        defaultIssueTypeId: Optional[str] = None,
        description: Optional[str] = None,
        name: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update issue type scheme\n\nHTTP PUT /rest/api/3/issuetypescheme/{issueTypeSchemeId}\nPath params:\n  - issueTypeSchemeId (int)\nBody (application/json) fields:\n  - defaultIssueTypeId (str, optional)\n  - description (str, optional)\n  - name (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueTypeSchemeId': issueTypeSchemeId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if defaultIssueTypeId is not None:
            _body['defaultIssueTypeId'] = defaultIssueTypeId
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        rel_path = '/rest/api/3/issuetypescheme/{issueTypeSchemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_issue_types_to_issue_type_scheme(
        self,
        issueTypeSchemeId: int,
        issueTypeIds: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add issue types to issue type scheme\n\nHTTP PUT /rest/api/3/issuetypescheme/{issueTypeSchemeId}/issuetype\nPath params:\n  - issueTypeSchemeId (int)\nBody (application/json) fields:\n  - issueTypeIds (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueTypeSchemeId': issueTypeSchemeId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['issueTypeIds'] = issueTypeIds
        rel_path = '/rest/api/3/issuetypescheme/{issueTypeSchemeId}/issuetype'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def reorder_issue_types_in_issue_type_scheme(
        self,
        issueTypeSchemeId: int,
        issueTypeIds: list[str],
        after: Optional[str] = None,
        position: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Change order of issue types\n\nHTTP PUT /rest/api/3/issuetypescheme/{issueTypeSchemeId}/issuetype/move\nPath params:\n  - issueTypeSchemeId (int)\nBody (application/json) fields:\n  - after (str, optional)\n  - issueTypeIds (list[str], required)\n  - position (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueTypeSchemeId': issueTypeSchemeId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if after is not None:
            _body['after'] = after
        _body['issueTypeIds'] = issueTypeIds
        if position is not None:
            _body['position'] = position
        rel_path = '/rest/api/3/issuetypescheme/{issueTypeSchemeId}/issuetype/move'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_issue_type_from_issue_type_scheme(
        self,
        issueTypeSchemeId: int,
        issueTypeId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove issue type from issue type scheme\n\nHTTP DELETE /rest/api/3/issuetypescheme/{issueTypeSchemeId}/issuetype/{issueTypeId}\nPath params:\n  - issueTypeSchemeId (int)\n  - issueTypeId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueTypeSchemeId': issueTypeSchemeId,
            'issueTypeId': issueTypeId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuetypescheme/{issueTypeSchemeId}/issuetype/{issueTypeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_type_screen_schemes(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        id: Optional[list[int]] = None,
        queryString: Optional[str] = None,
        orderBy: Optional[str] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue type screen schemes\n\nHTTP GET /rest/api/3/issuetypescreenscheme\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - id (list[int], optional)\n  - queryString (str, optional)\n  - orderBy (str, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        if queryString is not None:
            _query['queryString'] = queryString
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/issuetypescreenscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_issue_type_screen_scheme(
        self,
        issueTypeMappings: list[Dict[str, Any]],
        name: str,
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create issue type screen scheme\n\nHTTP POST /rest/api/3/issuetypescreenscheme\nBody (application/json) fields:\n  - description (str, optional)\n  - issueTypeMappings (list[Dict[str, Any]], required)\n  - name (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['issueTypeMappings'] = issueTypeMappings
        _body['name'] = name
        rel_path = '/rest/api/3/issuetypescreenscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_type_screen_scheme_mappings(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        issueTypeScreenSchemeId: Optional[list[int]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue type screen scheme items\n\nHTTP GET /rest/api/3/issuetypescreenscheme/mapping\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - issueTypeScreenSchemeId (list[int], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if issueTypeScreenSchemeId is not None:
            _query['issueTypeScreenSchemeId'] = issueTypeScreenSchemeId
        _body = None
        rel_path = '/rest/api/3/issuetypescreenscheme/mapping'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_type_screen_scheme_project_associations(
        self,
        projectId: list[int],
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue type screen schemes for projects\n\nHTTP GET /rest/api/3/issuetypescreenscheme/project\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - projectId (list[int], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _query['projectId'] = projectId
        _body = None
        rel_path = '/rest/api/3/issuetypescreenscheme/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def assign_issue_type_screen_scheme_to_project(
        self,
        issueTypeScreenSchemeId: Optional[str] = None,
        projectId: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Assign issue type screen scheme to project\n\nHTTP PUT /rest/api/3/issuetypescreenscheme/project\nBody (application/json) fields:\n  - issueTypeScreenSchemeId (str, optional)\n  - projectId (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if issueTypeScreenSchemeId is not None:
            _body['issueTypeScreenSchemeId'] = issueTypeScreenSchemeId
        if projectId is not None:
            _body['projectId'] = projectId
        rel_path = '/rest/api/3/issuetypescreenscheme/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_issue_type_screen_scheme(
        self,
        issueTypeScreenSchemeId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete issue type screen scheme\n\nHTTP DELETE /rest/api/3/issuetypescreenscheme/{issueTypeScreenSchemeId}\nPath params:\n  - issueTypeScreenSchemeId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueTypeScreenSchemeId': issueTypeScreenSchemeId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/issuetypescreenscheme/{issueTypeScreenSchemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_issue_type_screen_scheme(
        self,
        issueTypeScreenSchemeId: str,
        description: Optional[str] = None,
        name: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update issue type screen scheme\n\nHTTP PUT /rest/api/3/issuetypescreenscheme/{issueTypeScreenSchemeId}\nPath params:\n  - issueTypeScreenSchemeId (str)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueTypeScreenSchemeId': issueTypeScreenSchemeId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        rel_path = '/rest/api/3/issuetypescreenscheme/{issueTypeScreenSchemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def append_mappings_for_issue_type_screen_scheme(
        self,
        issueTypeScreenSchemeId: str,
        issueTypeMappings: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Append mappings to issue type screen scheme\n\nHTTP PUT /rest/api/3/issuetypescreenscheme/{issueTypeScreenSchemeId}/mapping\nPath params:\n  - issueTypeScreenSchemeId (str)\nBody (application/json) fields:\n  - issueTypeMappings (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueTypeScreenSchemeId': issueTypeScreenSchemeId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['issueTypeMappings'] = issueTypeMappings
        rel_path = '/rest/api/3/issuetypescreenscheme/{issueTypeScreenSchemeId}/mapping'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_default_screen_scheme(
        self,
        issueTypeScreenSchemeId: str,
        screenSchemeId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update issue type screen scheme default screen scheme\n\nHTTP PUT /rest/api/3/issuetypescreenscheme/{issueTypeScreenSchemeId}/mapping/default\nPath params:\n  - issueTypeScreenSchemeId (str)\nBody (application/json) fields:\n  - screenSchemeId (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueTypeScreenSchemeId': issueTypeScreenSchemeId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['screenSchemeId'] = screenSchemeId
        rel_path = '/rest/api/3/issuetypescreenscheme/{issueTypeScreenSchemeId}/mapping/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_mappings_from_issue_type_screen_scheme(
        self,
        issueTypeScreenSchemeId: str,
        issueTypeIds: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove mappings from issue type screen scheme\n\nHTTP POST /rest/api/3/issuetypescreenscheme/{issueTypeScreenSchemeId}/mapping/remove\nPath params:\n  - issueTypeScreenSchemeId (str)\nBody (application/json) fields:\n  - issueTypeIds (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'issueTypeScreenSchemeId': issueTypeScreenSchemeId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['issueTypeIds'] = issueTypeIds
        rel_path = '/rest/api/3/issuetypescreenscheme/{issueTypeScreenSchemeId}/mapping/remove'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_projects_for_issue_type_screen_scheme(
        self,
        issueTypeScreenSchemeId: int,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        query: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue type screen scheme projects\n\nHTTP GET /rest/api/3/issuetypescreenscheme/{issueTypeScreenSchemeId}/project\nPath params:\n  - issueTypeScreenSchemeId (int)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - query (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'issueTypeScreenSchemeId': issueTypeScreenSchemeId,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if query is not None:
            _query['query'] = query
        _body = None
        rel_path = '/rest/api/3/issuetypescreenscheme/{issueTypeScreenSchemeId}/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_auto_complete(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get field reference data (GET)\n\nHTTP GET /rest/api/3/jql/autocompletedata"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/jql/autocompletedata'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_auto_complete_post(
        self,
        includeCollapsedFields: Optional[bool] = None,
        projectIds: Optional[list[int]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get field reference data (POST)\n\nHTTP POST /rest/api/3/jql/autocompletedata\nBody (application/json) fields:\n  - includeCollapsedFields (bool, optional)\n  - projectIds (list[int], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if includeCollapsedFields is not None:
            _body['includeCollapsedFields'] = includeCollapsedFields
        if projectIds is not None:
            _body['projectIds'] = projectIds
        rel_path = '/rest/api/3/jql/autocompletedata'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_field_auto_complete_for_query_string(
        self,
        fieldName: Optional[str] = None,
        fieldValue: Optional[str] = None,
        predicateName: Optional[str] = None,
        predicateValue: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get field auto complete suggestions\n\nHTTP GET /rest/api/3/jql/autocompletedata/suggestions\nQuery params:\n  - fieldName (str, optional)\n  - fieldValue (str, optional)\n  - predicateName (str, optional)\n  - predicateValue (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if fieldName is not None:
            _query['fieldName'] = fieldName
        if fieldValue is not None:
            _query['fieldValue'] = fieldValue
        if predicateName is not None:
            _query['predicateName'] = predicateName
        if predicateValue is not None:
            _query['predicateValue'] = predicateValue
        _body = None
        rel_path = '/rest/api/3/jql/autocompletedata/suggestions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_precomputations(
        self,
        functionKey: Optional[list[str]] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get precomputations (apps)\n\nHTTP GET /rest/api/3/jql/function/computation\nQuery params:\n  - functionKey (list[str], optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - orderBy (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if functionKey is not None:
            _query['functionKey'] = functionKey
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if orderBy is not None:
            _query['orderBy'] = orderBy
        _body = None
        rel_path = '/rest/api/3/jql/function/computation'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_precomputations(
        self,
        skipNotFoundPrecomputations: Optional[bool] = None,
        values: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update precomputations (apps)\n\nHTTP POST /rest/api/3/jql/function/computation\nQuery params:\n  - skipNotFoundPrecomputations (bool, optional)\nBody (application/json) fields:\n  - values (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if skipNotFoundPrecomputations is not None:
            _query['skipNotFoundPrecomputations'] = skipNotFoundPrecomputations
        _body: Dict[str, Any] = {}
        if values is not None:
            _body['values'] = values
        rel_path = '/rest/api/3/jql/function/computation'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_precomputations_by_id(
        self,
        orderBy: Optional[str] = None,
        precomputationIDs: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get precomputations by ID (apps)\n\nHTTP POST /rest/api/3/jql/function/computation/search\nQuery params:\n  - orderBy (str, optional)\nBody (application/json) fields:\n  - precomputationIDs (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if orderBy is not None:
            _query['orderBy'] = orderBy
        _body: Dict[str, Any] = {}
        if precomputationIDs is not None:
            _body['precomputationIDs'] = precomputationIDs
        rel_path = '/rest/api/3/jql/function/computation/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def match_issues(
        self,
        issueIds: list[int],
        jqls: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Check issues against JQL\n\nHTTP POST /rest/api/3/jql/match\nBody (application/json) fields:\n  - issueIds (list[int], required)\n  - jqls (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['issueIds'] = issueIds
        _body['jqls'] = jqls
        rel_path = '/rest/api/3/jql/match'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def parse_jql_queries(
        self,
        validation: str,
        queries: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Parse JQL query\n\nHTTP POST /rest/api/3/jql/parse\nQuery params:\n  - validation (str, required)\nBody (application/json) fields:\n  - queries (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['validation'] = validation
        _body: Dict[str, Any] = {}
        _body['queries'] = queries
        rel_path = '/rest/api/3/jql/parse'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def migrate_queries(
        self,
        queryStrings: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Convert user identifiers to account IDs in JQL queries\n\nHTTP POST /rest/api/3/jql/pdcleaner\nBody (application/json) fields:\n  - queryStrings (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if queryStrings is not None:
            _body['queryStrings'] = queryStrings
        rel_path = '/rest/api/3/jql/pdcleaner'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def sanitise_jql_queries(
        self,
        queries: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Sanitize JQL queries\n\nHTTP POST /rest/api/3/jql/sanitize\nBody (application/json) fields:\n  - queries (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['queries'] = queries
        rel_path = '/rest/api/3/jql/sanitize'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_labels(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all labels\n\nHTTP GET /rest/api/3/label\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/label'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_approximate_license_count(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get approximate license count\n\nHTTP GET /rest/api/3/license/approximateLicenseCount"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/license/approximateLicenseCount'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_approximate_application_license_count(
        self,
        applicationKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get approximate application license count\n\nHTTP GET /rest/api/3/license/approximateLicenseCount/product/{applicationKey}\nPath params:\n  - applicationKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'applicationKey': applicationKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/license/approximateLicenseCount/product/{applicationKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_my_permissions(
        self,
        projectKey: Optional[str] = None,
        projectId: Optional[str] = None,
        issueKey: Optional[str] = None,
        issueId: Optional[str] = None,
        permissions: Optional[str] = None,
        projectUuid: Optional[str] = None,
        projectConfigurationUuid: Optional[str] = None,
        commentId: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get my permissions\n\nHTTP GET /rest/api/3/mypermissions\nQuery params:\n  - projectKey (str, optional)\n  - projectId (str, optional)\n  - issueKey (str, optional)\n  - issueId (str, optional)\n  - permissions (str, optional)\n  - projectUuid (str, optional)\n  - projectConfigurationUuid (str, optional)\n  - commentId (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if projectKey is not None:
            _query['projectKey'] = projectKey
        if projectId is not None:
            _query['projectId'] = projectId
        if issueKey is not None:
            _query['issueKey'] = issueKey
        if issueId is not None:
            _query['issueId'] = issueId
        if permissions is not None:
            _query['permissions'] = permissions
        if projectUuid is not None:
            _query['projectUuid'] = projectUuid
        if projectConfigurationUuid is not None:
            _query['projectConfigurationUuid'] = projectConfigurationUuid
        if commentId is not None:
            _query['commentId'] = commentId
        _body = None
        rel_path = '/rest/api/3/mypermissions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_preference(
        self,
        key: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete preference\n\nHTTP DELETE /rest/api/3/mypreferences\nQuery params:\n  - key (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['key'] = key
        _body = None
        rel_path = '/rest/api/3/mypreferences'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_preference(
        self,
        key: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get preference\n\nHTTP GET /rest/api/3/mypreferences\nQuery params:\n  - key (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['key'] = key
        _body = None
        rel_path = '/rest/api/3/mypreferences'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_preference(
        self,
        key: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set preference\n\nHTTP PUT /rest/api/3/mypreferences\nQuery params:\n  - key (str, required)\nBody: application/json (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['key'] = key
        _body = body
        rel_path = '/rest/api/3/mypreferences'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_locale(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get locale\n\nHTTP GET /rest/api/3/mypreferences/locale"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/mypreferences/locale'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_locale(
        self,
        locale: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set locale\n\nHTTP PUT /rest/api/3/mypreferences/locale\nBody (application/json) fields:\n  - locale (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if locale is not None:
            _body['locale'] = locale
        rel_path = '/rest/api/3/mypreferences/locale'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_current_user(
        self,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get current user\n\nHTTP GET /rest/api/3/myself\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/myself'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_notification_schemes(
        self,
        startAt: Optional[str] = None,
        maxResults: Optional[str] = None,
        id: Optional[list[str]] = None,
        projectId: Optional[list[str]] = None,
        onlyDefault: Optional[bool] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get notification schemes paginated\n\nHTTP GET /rest/api/3/notificationscheme\nQuery params:\n  - startAt (str, optional)\n  - maxResults (str, optional)\n  - id (list[str], optional)\n  - projectId (list[str], optional)\n  - onlyDefault (bool, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        if projectId is not None:
            _query['projectId'] = projectId
        if onlyDefault is not None:
            _query['onlyDefault'] = onlyDefault
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/notificationscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_notification_scheme(
        self,
        name: str,
        description: Optional[str] = None,
        notificationSchemeEvents: Optional[list[Dict[str, Any]]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create notification scheme\n\nHTTP POST /rest/api/3/notificationscheme\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, required)\n  - notificationSchemeEvents (list[Dict[str, Any]], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['name'] = name
        if notificationSchemeEvents is not None:
            _body['notificationSchemeEvents'] = notificationSchemeEvents
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/notificationscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_notification_scheme_to_project_mappings(
        self,
        startAt: Optional[str] = None,
        maxResults: Optional[str] = None,
        notificationSchemeId: Optional[list[str]] = None,
        projectId: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get projects using notification schemes paginated\n\nHTTP GET /rest/api/3/notificationscheme/project\nQuery params:\n  - startAt (str, optional)\n  - maxResults (str, optional)\n  - notificationSchemeId (list[str], optional)\n  - projectId (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if notificationSchemeId is not None:
            _query['notificationSchemeId'] = notificationSchemeId
        if projectId is not None:
            _query['projectId'] = projectId
        _body = None
        rel_path = '/rest/api/3/notificationscheme/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_notification_scheme(
        self,
        id: int,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get notification scheme\n\nHTTP GET /rest/api/3/notificationscheme/{id}\nPath params:\n  - id (int)\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/notificationscheme/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_notification_scheme(
        self,
        id: str,
        description: Optional[str] = None,
        name: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update notification scheme\n\nHTTP PUT /rest/api/3/notificationscheme/{id}\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/notificationscheme/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_notifications(
        self,
        id: str,
        notificationSchemeEvents: list[Dict[str, Any]],
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add notifications to notification scheme\n\nHTTP PUT /rest/api/3/notificationscheme/{id}/notification\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - notificationSchemeEvents (list[Dict[str, Any]], required)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['notificationSchemeEvents'] = notificationSchemeEvents
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/notificationscheme/{id}/notification'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_notification_scheme(
        self,
        notificationSchemeId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete notification scheme\n\nHTTP DELETE /rest/api/3/notificationscheme/{notificationSchemeId}\nPath params:\n  - notificationSchemeId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'notificationSchemeId': notificationSchemeId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/notificationscheme/{notificationSchemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_notification_from_notification_scheme(
        self,
        notificationSchemeId: str,
        notificationId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove notification from notification scheme\n\nHTTP DELETE /rest/api/3/notificationscheme/{notificationSchemeId}/notification/{notificationId}\nPath params:\n  - notificationSchemeId (str)\n  - notificationId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'notificationSchemeId': notificationSchemeId,
            'notificationId': notificationId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/notificationscheme/{notificationSchemeId}/notification/{notificationId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_permissions(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all permissions\n\nHTTP GET /rest/api/3/permissions"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/permissions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_bulk_permissions(
        self,
        accountId: Optional[str] = None,
        globalPermissions: Optional[list[str]] = None,
        projectPermissions: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get bulk permissions\n\nHTTP POST /rest/api/3/permissions/check\nBody (application/json) fields:\n  - accountId (str, optional)\n  - globalPermissions (list[str], optional)\n  - projectPermissions (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if accountId is not None:
            _body['accountId'] = accountId
        if globalPermissions is not None:
            _body['globalPermissions'] = globalPermissions
        if projectPermissions is not None:
            _body['projectPermissions'] = projectPermissions
        rel_path = '/rest/api/3/permissions/check'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_permitted_projects(
        self,
        permissions: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permitted projects\n\nHTTP POST /rest/api/3/permissions/project\nBody (application/json) fields:\n  - permissions (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['permissions'] = permissions
        rel_path = '/rest/api/3/permissions/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_permission_schemes(
        self,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all permission schemes\n\nHTTP GET /rest/api/3/permissionscheme\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/permissionscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_permission_scheme(
        self,
        name: str,
        expand: Optional[str] = None,
        description: Optional[str] = None,
        expand_body: Optional[str] = None,
        id: Optional[int] = None,
        permissions: Optional[list[Dict[str, Any]]] = None,
        scope: Optional[Dict[str, Any]] = None,
        self_: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create permission scheme\n\nHTTP POST /rest/api/3/permissionscheme\nQuery params:\n  - expand (str, optional)\nBody (application/json) fields:\n  - description (str, optional)\n  - expand (str, optional)\n  - id (int, optional)\n  - name (str, required)\n  - permissions (list[Dict[str, Any]], optional)\n  - scope (Dict[str, Any], optional)\n  - self (str, optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if expand_body is not None:
            _body['expand'] = expand_body
        if id is not None:
            _body['id'] = id
        _body['name'] = name
        if permissions is not None:
            _body['permissions'] = permissions
        if scope is not None:
            _body['scope'] = scope
        if self_ is not None:
            _body['self'] = self_
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/permissionscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_permission_scheme(
        self,
        schemeId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete permission scheme\n\nHTTP DELETE /rest/api/3/permissionscheme/{schemeId}\nPath params:\n  - schemeId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/permissionscheme/{schemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_permission_scheme(
        self,
        schemeId: int,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permission scheme\n\nHTTP GET /rest/api/3/permissionscheme/{schemeId}\nPath params:\n  - schemeId (int)\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/permissionscheme/{schemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_permission_scheme(
        self,
        schemeId: int,
        name: str,
        expand: Optional[str] = None,
        description: Optional[str] = None,
        expand_body: Optional[str] = None,
        id: Optional[int] = None,
        permissions: Optional[list[Dict[str, Any]]] = None,
        scope: Optional[Dict[str, Any]] = None,
        self_: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update permission scheme\n\nHTTP PUT /rest/api/3/permissionscheme/{schemeId}\nPath params:\n  - schemeId (int)\nQuery params:\n  - expand (str, optional)\nBody (application/json) fields:\n  - description (str, optional)\n  - expand (str, optional)\n  - id (int, optional)\n  - name (str, required)\n  - permissions (list[Dict[str, Any]], optional)\n  - scope (Dict[str, Any], optional)\n  - self (str, optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if expand_body is not None:
            _body['expand'] = expand_body
        if id is not None:
            _body['id'] = id
        _body['name'] = name
        if permissions is not None:
            _body['permissions'] = permissions
        if scope is not None:
            _body['scope'] = scope
        if self_ is not None:
            _body['self'] = self_
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/permissionscheme/{schemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_permission_scheme_grants(
        self,
        schemeId: int,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permission scheme grants\n\nHTTP GET /rest/api/3/permissionscheme/{schemeId}/permission\nPath params:\n  - schemeId (int)\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/permissionscheme/{schemeId}/permission'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_permission_grant(
        self,
        schemeId: int,
        expand: Optional[str] = None,
        holder: Optional[Dict[str, Any]] = None,
        id: Optional[int] = None,
        permission: Optional[str] = None,
        self_: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create permission grant\n\nHTTP POST /rest/api/3/permissionscheme/{schemeId}/permission\nPath params:\n  - schemeId (int)\nQuery params:\n  - expand (str, optional)\nBody (application/json) fields:\n  - holder (Dict[str, Any], optional)\n  - id (int, optional)\n  - permission (str, optional)\n  - self (str, optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body: Dict[str, Any] = {}
        if holder is not None:
            _body['holder'] = holder
        if id is not None:
            _body['id'] = id
        if permission is not None:
            _body['permission'] = permission
        if self_ is not None:
            _body['self'] = self_
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/permissionscheme/{schemeId}/permission'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_permission_scheme_entity(
        self,
        schemeId: int,
        permissionId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete permission scheme grant\n\nHTTP DELETE /rest/api/3/permissionscheme/{schemeId}/permission/{permissionId}\nPath params:\n  - schemeId (int)\n  - permissionId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
            'permissionId': permissionId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/permissionscheme/{schemeId}/permission/{permissionId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_permission_scheme_grant(
        self,
        schemeId: int,
        permissionId: int,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permission scheme grant\n\nHTTP GET /rest/api/3/permissionscheme/{schemeId}/permission/{permissionId}\nPath params:\n  - schemeId (int)\n  - permissionId (int)\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
            'permissionId': permissionId,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/permissionscheme/{schemeId}/permission/{permissionId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_plans(
        self,
        includeTrashed: Optional[bool] = None,
        includeArchived: Optional[bool] = None,
        cursor: Optional[str] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get plans paginated\n\nHTTP GET /rest/api/3/plans/plan\nQuery params:\n  - includeTrashed (bool, optional)\n  - includeArchived (bool, optional)\n  - cursor (str, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if includeTrashed is not None:
            _query['includeTrashed'] = includeTrashed
        if includeArchived is not None:
            _query['includeArchived'] = includeArchived
        if cursor is not None:
            _query['cursor'] = cursor
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/plans/plan'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_plan(
        self,
        issueSources: list[Dict[str, Any]],
        name: str,
        scheduling: Dict[str, Any],
        useGroupId: Optional[bool] = None,
        crossProjectReleases: Optional[list[Dict[str, Any]]] = None,
        customFields: Optional[list[Dict[str, Any]]] = None,
        exclusionRules: Optional[Dict[str, Any]] = None,
        leadAccountId: Optional[str] = None,
        permissions: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create plan\n\nHTTP POST /rest/api/3/plans/plan\nQuery params:\n  - useGroupId (bool, optional)\nBody (application/json) fields:\n  - crossProjectReleases (list[Dict[str, Any]], optional)\n  - customFields (list[Dict[str, Any]], optional)\n  - exclusionRules (Dict[str, Any], optional)\n  - issueSources (list[Dict[str, Any]], required)\n  - leadAccountId (str, optional)\n  - name (str, required)\n  - permissions (list[Dict[str, Any]], optional)\n  - scheduling (Dict[str, Any], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if useGroupId is not None:
            _query['useGroupId'] = useGroupId
        _body: Dict[str, Any] = {}
        if crossProjectReleases is not None:
            _body['crossProjectReleases'] = crossProjectReleases
        if customFields is not None:
            _body['customFields'] = customFields
        if exclusionRules is not None:
            _body['exclusionRules'] = exclusionRules
        _body['issueSources'] = issueSources
        if leadAccountId is not None:
            _body['leadAccountId'] = leadAccountId
        _body['name'] = name
        if permissions is not None:
            _body['permissions'] = permissions
        _body['scheduling'] = scheduling
        rel_path = '/rest/api/3/plans/plan'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_plan(
        self,
        planId: int,
        useGroupId: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get plan\n\nHTTP GET /rest/api/3/plans/plan/{planId}\nPath params:\n  - planId (int)\nQuery params:\n  - useGroupId (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'planId': planId,
        }
        _query: Dict[str, Any] = {}
        if useGroupId is not None:
            _query['useGroupId'] = useGroupId
        _body = None
        rel_path = '/rest/api/3/plans/plan/{planId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_plan(
        self,
        planId: int,
        useGroupId: Optional[bool] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update plan\n\nHTTP PUT /rest/api/3/plans/plan/{planId}\nPath params:\n  - planId (int)\nQuery params:\n  - useGroupId (bool, optional)\nBody: application/json-patch+json (Dict[str, Any])"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json-patch+json')
        _path: Dict[str, Any] = {
            'planId': planId,
        }
        _query: Dict[str, Any] = {}
        if useGroupId is not None:
            _query['useGroupId'] = useGroupId
        _body = body
        rel_path = '/rest/api/3/plans/plan/{planId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def archive_plan(
        self,
        planId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Archive plan\n\nHTTP PUT /rest/api/3/plans/plan/{planId}/archive\nPath params:\n  - planId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'planId': planId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/plans/plan/{planId}/archive'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def duplicate_plan(
        self,
        planId: int,
        name: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Duplicate plan\n\nHTTP POST /rest/api/3/plans/plan/{planId}/duplicate\nPath params:\n  - planId (int)\nBody (application/json) fields:\n  - name (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'planId': planId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['name'] = name
        rel_path = '/rest/api/3/plans/plan/{planId}/duplicate'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_teams(
        self,
        planId: int,
        cursor: Optional[str] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get teams in plan paginated\n\nHTTP GET /rest/api/3/plans/plan/{planId}/team\nPath params:\n  - planId (int)\nQuery params:\n  - cursor (str, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'planId': planId,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/plans/plan/{planId}/team'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_atlassian_team(
        self,
        planId: int,
        id: str,
        planningStyle: str,
        capacity: Optional[float] = None,
        issueSourceId: Optional[int] = None,
        sprintLength: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add Atlassian team to plan\n\nHTTP POST /rest/api/3/plans/plan/{planId}/team/atlassian\nPath params:\n  - planId (int)\nBody (application/json) fields:\n  - capacity (float, optional)\n  - id (str, required)\n  - issueSourceId (int, optional)\n  - planningStyle (str, required)\n  - sprintLength (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'planId': planId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if capacity is not None:
            _body['capacity'] = capacity
        _body['id'] = id
        if issueSourceId is not None:
            _body['issueSourceId'] = issueSourceId
        _body['planningStyle'] = planningStyle
        if sprintLength is not None:
            _body['sprintLength'] = sprintLength
        rel_path = '/rest/api/3/plans/plan/{planId}/team/atlassian'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_atlassian_team(
        self,
        planId: int,
        atlassianTeamId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove Atlassian team from plan\n\nHTTP DELETE /rest/api/3/plans/plan/{planId}/team/atlassian/{atlassianTeamId}\nPath params:\n  - planId (int)\n  - atlassianTeamId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'planId': planId,
            'atlassianTeamId': atlassianTeamId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/plans/plan/{planId}/team/atlassian/{atlassianTeamId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_atlassian_team(
        self,
        planId: int,
        atlassianTeamId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Atlassian team in plan\n\nHTTP GET /rest/api/3/plans/plan/{planId}/team/atlassian/{atlassianTeamId}\nPath params:\n  - planId (int)\n  - atlassianTeamId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'planId': planId,
            'atlassianTeamId': atlassianTeamId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/plans/plan/{planId}/team/atlassian/{atlassianTeamId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_atlassian_team(
        self,
        planId: int,
        atlassianTeamId: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update Atlassian team in plan\n\nHTTP PUT /rest/api/3/plans/plan/{planId}/team/atlassian/{atlassianTeamId}\nPath params:\n  - planId (int)\n  - atlassianTeamId (str)\nBody: application/json-patch+json (Dict[str, Any])"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json-patch+json')
        _path: Dict[str, Any] = {
            'planId': planId,
            'atlassianTeamId': atlassianTeamId,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/api/3/plans/plan/{planId}/team/atlassian/{atlassianTeamId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_plan_only_team(
        self,
        planId: int,
        name: str,
        planningStyle: str,
        capacity: Optional[float] = None,
        issueSourceId: Optional[int] = None,
        memberAccountIds: Optional[list[str]] = None,
        sprintLength: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create plan-only team\n\nHTTP POST /rest/api/3/plans/plan/{planId}/team/planonly\nPath params:\n  - planId (int)\nBody (application/json) fields:\n  - capacity (float, optional)\n  - issueSourceId (int, optional)\n  - memberAccountIds (list[str], optional)\n  - name (str, required)\n  - planningStyle (str, required)\n  - sprintLength (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'planId': planId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if capacity is not None:
            _body['capacity'] = capacity
        if issueSourceId is not None:
            _body['issueSourceId'] = issueSourceId
        if memberAccountIds is not None:
            _body['memberAccountIds'] = memberAccountIds
        _body['name'] = name
        _body['planningStyle'] = planningStyle
        if sprintLength is not None:
            _body['sprintLength'] = sprintLength
        rel_path = '/rest/api/3/plans/plan/{planId}/team/planonly'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_plan_only_team(
        self,
        planId: int,
        planOnlyTeamId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete plan-only team\n\nHTTP DELETE /rest/api/3/plans/plan/{planId}/team/planonly/{planOnlyTeamId}\nPath params:\n  - planId (int)\n  - planOnlyTeamId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'planId': planId,
            'planOnlyTeamId': planOnlyTeamId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/plans/plan/{planId}/team/planonly/{planOnlyTeamId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_plan_only_team(
        self,
        planId: int,
        planOnlyTeamId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get plan-only team\n\nHTTP GET /rest/api/3/plans/plan/{planId}/team/planonly/{planOnlyTeamId}\nPath params:\n  - planId (int)\n  - planOnlyTeamId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'planId': planId,
            'planOnlyTeamId': planOnlyTeamId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/plans/plan/{planId}/team/planonly/{planOnlyTeamId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_plan_only_team(
        self,
        planId: int,
        planOnlyTeamId: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update plan-only team\n\nHTTP PUT /rest/api/3/plans/plan/{planId}/team/planonly/{planOnlyTeamId}\nPath params:\n  - planId (int)\n  - planOnlyTeamId (int)\nBody: application/json-patch+json (Dict[str, Any])"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json-patch+json')
        _path: Dict[str, Any] = {
            'planId': planId,
            'planOnlyTeamId': planOnlyTeamId,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/api/3/plans/plan/{planId}/team/planonly/{planOnlyTeamId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def trash_plan(
        self,
        planId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Trash plan\n\nHTTP PUT /rest/api/3/plans/plan/{planId}/trash\nPath params:\n  - planId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'planId': planId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/plans/plan/{planId}/trash'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_priorities(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get priorities\n\nHTTP GET /rest/api/3/priority"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/priority'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_priority(
        self,
        name: str,
        statusColor: str,
        avatarId: Optional[int] = None,
        description: Optional[str] = None,
        iconUrl: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create priority\n\nHTTP POST /rest/api/3/priority\nBody (application/json) fields:\n  - avatarId (int, optional)\n  - description (str, optional)\n  - iconUrl (str, optional)\n  - name (str, required)\n  - statusColor (str, required)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if avatarId is not None:
            _body['avatarId'] = avatarId
        if description is not None:
            _body['description'] = description
        if iconUrl is not None:
            _body['iconUrl'] = iconUrl
        _body['name'] = name
        _body['statusColor'] = statusColor
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/priority'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_default_priority(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set default priority\n\nHTTP PUT /rest/api/3/priority/default\nBody (application/json) fields:\n  - id (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['id'] = id
        rel_path = '/rest/api/3/priority/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def move_priorities(
        self,
        ids: list[str],
        after: Optional[str] = None,
        position: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Move priorities\n\nHTTP PUT /rest/api/3/priority/move\nBody (application/json) fields:\n  - after (str, optional)\n  - ids (list[str], required)\n  - position (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if after is not None:
            _body['after'] = after
        _body['ids'] = ids
        if position is not None:
            _body['position'] = position
        rel_path = '/rest/api/3/priority/move'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def search_priorities(
        self,
        startAt: Optional[str] = None,
        maxResults: Optional[str] = None,
        id: Optional[list[str]] = None,
        projectId: Optional[list[str]] = None,
        priorityName: Optional[str] = None,
        onlyDefault: Optional[bool] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Search priorities\n\nHTTP GET /rest/api/3/priority/search\nQuery params:\n  - startAt (str, optional)\n  - maxResults (str, optional)\n  - id (list[str], optional)\n  - projectId (list[str], optional)\n  - priorityName (str, optional)\n  - onlyDefault (bool, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        if projectId is not None:
            _query['projectId'] = projectId
        if priorityName is not None:
            _query['priorityName'] = priorityName
        if onlyDefault is not None:
            _query['onlyDefault'] = onlyDefault
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/priority/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_priority(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete priority\n\nHTTP DELETE /rest/api/3/priority/{id}\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/priority/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_priority(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get priority\n\nHTTP GET /rest/api/3/priority/{id}\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/priority/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_priority(
        self,
        id: str,
        avatarId: Optional[int] = None,
        description: Optional[str] = None,
        iconUrl: Optional[str] = None,
        name: Optional[str] = None,
        statusColor: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update priority\n\nHTTP PUT /rest/api/3/priority/{id}\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - avatarId (int, optional)\n  - description (str, optional)\n  - iconUrl (str, optional)\n  - name (str, optional)\n  - statusColor (str, optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if avatarId is not None:
            _body['avatarId'] = avatarId
        if description is not None:
            _body['description'] = description
        if iconUrl is not None:
            _body['iconUrl'] = iconUrl
        if name is not None:
            _body['name'] = name
        if statusColor is not None:
            _body['statusColor'] = statusColor
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/priority/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_priority_schemes(
        self,
        startAt: Optional[str] = None,
        maxResults: Optional[str] = None,
        priorityId: Optional[list[int]] = None,
        schemeId: Optional[list[int]] = None,
        schemeName: Optional[str] = None,
        onlyDefault: Optional[bool] = None,
        orderBy: Optional[str] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get priority schemes\n\nHTTP GET /rest/api/3/priorityscheme\nQuery params:\n  - startAt (str, optional)\n  - maxResults (str, optional)\n  - priorityId (list[int], optional)\n  - schemeId (list[int], optional)\n  - schemeName (str, optional)\n  - onlyDefault (bool, optional)\n  - orderBy (str, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if priorityId is not None:
            _query['priorityId'] = priorityId
        if schemeId is not None:
            _query['schemeId'] = schemeId
        if schemeName is not None:
            _query['schemeName'] = schemeName
        if onlyDefault is not None:
            _query['onlyDefault'] = onlyDefault
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/priorityscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_priority_scheme(
        self,
        defaultPriorityId: int,
        name: str,
        priorityIds: list[int],
        description: Optional[str] = None,
        mappings: Optional[Dict[str, Any]] = None,
        projectIds: Optional[list[int]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create priority scheme\n\nHTTP POST /rest/api/3/priorityscheme\nBody (application/json) fields:\n  - defaultPriorityId (int, required)\n  - description (str, optional)\n  - mappings (Dict[str, Any], optional)\n  - name (str, required)\n  - priorityIds (list[int], required)\n  - projectIds (list[int], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['defaultPriorityId'] = defaultPriorityId
        if description is not None:
            _body['description'] = description
        if mappings is not None:
            _body['mappings'] = mappings
        _body['name'] = name
        _body['priorityIds'] = priorityIds
        if projectIds is not None:
            _body['projectIds'] = projectIds
        rel_path = '/rest/api/3/priorityscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def suggested_priorities_for_mappings(
        self,
        maxResults: Optional[int] = None,
        priorities: Optional[Dict[str, Any]] = None,
        projects: Optional[Dict[str, Any]] = None,
        schemeId: Optional[int] = None,
        startAt: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Suggested priorities for mappings\n\nHTTP POST /rest/api/3/priorityscheme/mappings\nBody (application/json) fields:\n  - maxResults (int, optional)\n  - priorities (Dict[str, Any], optional)\n  - projects (Dict[str, Any], optional)\n  - schemeId (int, optional)\n  - startAt (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if maxResults is not None:
            _body['maxResults'] = maxResults
        if priorities is not None:
            _body['priorities'] = priorities
        if projects is not None:
            _body['projects'] = projects
        if schemeId is not None:
            _body['schemeId'] = schemeId
        if startAt is not None:
            _body['startAt'] = startAt
        rel_path = '/rest/api/3/priorityscheme/mappings'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_available_priorities_by_priority_scheme(
        self,
        schemeId: str,
        startAt: Optional[str] = None,
        maxResults: Optional[str] = None,
        query: Optional[str] = None,
        exclude: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get available priorities by priority scheme\n\nHTTP GET /rest/api/3/priorityscheme/priorities/available\nQuery params:\n  - startAt (str, optional)\n  - maxResults (str, optional)\n  - query (str, optional)\n  - schemeId (str, required)\n  - exclude (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if query is not None:
            _query['query'] = query
        _query['schemeId'] = schemeId
        if exclude is not None:
            _query['exclude'] = exclude
        _body = None
        rel_path = '/rest/api/3/priorityscheme/priorities/available'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_priority_scheme(
        self,
        schemeId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete priority scheme\n\nHTTP DELETE /rest/api/3/priorityscheme/{schemeId}\nPath params:\n  - schemeId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/priorityscheme/{schemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_priority_scheme(
        self,
        schemeId: int,
        defaultPriorityId: Optional[int] = None,
        description: Optional[str] = None,
        mappings: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        priorities: Optional[Dict[str, Any]] = None,
        projects: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update priority scheme\n\nHTTP PUT /rest/api/3/priorityscheme/{schemeId}\nPath params:\n  - schemeId (int)\nBody (application/json) fields:\n  - defaultPriorityId (int, optional)\n  - description (str, optional)\n  - mappings (Dict[str, Any], optional)\n  - name (str, optional)\n  - priorities (Dict[str, Any], optional)\n  - projects (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if defaultPriorityId is not None:
            _body['defaultPriorityId'] = defaultPriorityId
        if description is not None:
            _body['description'] = description
        if mappings is not None:
            _body['mappings'] = mappings
        if name is not None:
            _body['name'] = name
        if priorities is not None:
            _body['priorities'] = priorities
        if projects is not None:
            _body['projects'] = projects
        rel_path = '/rest/api/3/priorityscheme/{schemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_priorities_by_priority_scheme(
        self,
        schemeId: str,
        startAt: Optional[str] = None,
        maxResults: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get priorities by priority scheme\n\nHTTP GET /rest/api/3/priorityscheme/{schemeId}/priorities\nPath params:\n  - schemeId (str)\nQuery params:\n  - startAt (str, optional)\n  - maxResults (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/priorityscheme/{schemeId}/priorities'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_projects_by_priority_scheme(
        self,
        schemeId: str,
        startAt: Optional[str] = None,
        maxResults: Optional[str] = None,
        projectId: Optional[list[int]] = None,
        query: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get projects by priority scheme\n\nHTTP GET /rest/api/3/priorityscheme/{schemeId}/projects\nPath params:\n  - schemeId (str)\nQuery params:\n  - startAt (str, optional)\n  - maxResults (str, optional)\n  - projectId (list[int], optional)\n  - query (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'schemeId': schemeId,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if projectId is not None:
            _query['projectId'] = projectId
        if query is not None:
            _query['query'] = query
        _body = None
        rel_path = '/rest/api/3/priorityscheme/{schemeId}/projects'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_projects(
        self,
        expand: Optional[str] = None,
        recent: Optional[int] = None,
        properties: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all projects\n\nHTTP GET /rest/api/3/project\nQuery params:\n  - expand (str, optional)\n  - recent (int, optional)\n  - properties (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        if recent is not None:
            _query['recent'] = recent
        if properties is not None:
            _query['properties'] = properties
        _body = None
        rel_path = '/rest/api/3/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_project(
        self,
        key: str,
        name: str,
        assigneeType: Optional[str] = None,
        avatarId: Optional[int] = None,
        categoryId: Optional[int] = None,
        description: Optional[str] = None,
        fieldConfigurationScheme: Optional[int] = None,
        issueSecurityScheme: Optional[int] = None,
        issueTypeScheme: Optional[int] = None,
        issueTypeScreenScheme: Optional[int] = None,
        lead: Optional[str] = None,
        leadAccountId: Optional[str] = None,
        notificationScheme: Optional[int] = None,
        permissionScheme: Optional[int] = None,
        projectTemplateKey: Optional[str] = None,
        projectTypeKey: Optional[str] = None,
        url: Optional[str] = None,
        workflowScheme: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create project\n\nHTTP POST /rest/api/3/project\nBody (application/json) fields:\n  - assigneeType (str, optional)\n  - avatarId (int, optional)\n  - categoryId (int, optional)\n  - description (str, optional)\n  - fieldConfigurationScheme (int, optional)\n  - issueSecurityScheme (int, optional)\n  - issueTypeScheme (int, optional)\n  - issueTypeScreenScheme (int, optional)\n  - key (str, required)\n  - lead (str, optional)\n  - leadAccountId (str, optional)\n  - name (str, required)\n  - notificationScheme (int, optional)\n  - permissionScheme (int, optional)\n  - projectTemplateKey (str, optional)\n  - projectTypeKey (str, optional)\n  - url (str, optional)\n  - workflowScheme (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if assigneeType is not None:
            _body['assigneeType'] = assigneeType
        if avatarId is not None:
            _body['avatarId'] = avatarId
        if categoryId is not None:
            _body['categoryId'] = categoryId
        if description is not None:
            _body['description'] = description
        if fieldConfigurationScheme is not None:
            _body['fieldConfigurationScheme'] = fieldConfigurationScheme
        if issueSecurityScheme is not None:
            _body['issueSecurityScheme'] = issueSecurityScheme
        if issueTypeScheme is not None:
            _body['issueTypeScheme'] = issueTypeScheme
        if issueTypeScreenScheme is not None:
            _body['issueTypeScreenScheme'] = issueTypeScreenScheme
        _body['key'] = key
        if lead is not None:
            _body['lead'] = lead
        if leadAccountId is not None:
            _body['leadAccountId'] = leadAccountId
        _body['name'] = name
        if notificationScheme is not None:
            _body['notificationScheme'] = notificationScheme
        if permissionScheme is not None:
            _body['permissionScheme'] = permissionScheme
        if projectTemplateKey is not None:
            _body['projectTemplateKey'] = projectTemplateKey
        if projectTypeKey is not None:
            _body['projectTypeKey'] = projectTypeKey
        if url is not None:
            _body['url'] = url
        if workflowScheme is not None:
            _body['workflowScheme'] = workflowScheme
        rel_path = '/rest/api/3/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_project_with_custom_template(
        self,
        details: Optional[Dict[str, Any]] = None,
        template: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create custom project\n\nHTTP POST /rest/api/3/project-template\nBody (application/json) fields:\n  - details (Dict[str, Any], optional)\n  - template (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if details is not None:
            _body['details'] = details
        if template is not None:
            _body['template'] = template
        rel_path = '/rest/api/3/project-template'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def edit_template(
        self,
        templateDescription: Optional[str] = None,
        templateGenerationOptions: Optional[Dict[str, Any]] = None,
        templateKey: Optional[str] = None,
        templateName: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Edit a custom project template\n\nHTTP PUT /rest/api/3/project-template/edit-template\nBody (application/json) fields:\n  - templateDescription (str, optional)\n  - templateGenerationOptions (Dict[str, Any], optional)\n  - templateKey (str, optional)\n  - templateName (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if templateDescription is not None:
            _body['templateDescription'] = templateDescription
        if templateGenerationOptions is not None:
            _body['templateGenerationOptions'] = templateGenerationOptions
        if templateKey is not None:
            _body['templateKey'] = templateKey
        if templateName is not None:
            _body['templateName'] = templateName
        rel_path = '/rest/api/3/project-template/edit-template'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def live_template(
        self,
        projectId: Optional[str] = None,
        templateKey: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Gets a custom project template\n\nHTTP GET /rest/api/3/project-template/live-template\nQuery params:\n  - projectId (str, optional)\n  - templateKey (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if projectId is not None:
            _query['projectId'] = projectId
        if templateKey is not None:
            _query['templateKey'] = templateKey
        _body = None
        rel_path = '/rest/api/3/project-template/live-template'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_template(
        self,
        templateKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Deletes a custom project template\n\nHTTP DELETE /rest/api/3/project-template/remove-template\nQuery params:\n  - templateKey (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['templateKey'] = templateKey
        _body = None
        rel_path = '/rest/api/3/project-template/remove-template'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def save_template(
        self,
        templateDescription: Optional[str] = None,
        templateFromProjectRequest: Optional[Dict[str, Any]] = None,
        templateName: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Save a custom project template\n\nHTTP POST /rest/api/3/project-template/save-template\nBody (application/json) fields:\n  - templateDescription (str, optional)\n  - templateFromProjectRequest (Dict[str, Any], optional)\n  - templateName (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if templateDescription is not None:
            _body['templateDescription'] = templateDescription
        if templateFromProjectRequest is not None:
            _body['templateFromProjectRequest'] = templateFromProjectRequest
        if templateName is not None:
            _body['templateName'] = templateName
        rel_path = '/rest/api/3/project-template/save-template'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_recent(
        self,
        expand: Optional[str] = None,
        properties: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get recent projects\n\nHTTP GET /rest/api/3/project/recent\nQuery params:\n  - expand (str, optional)\n  - properties (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        if properties is not None:
            _query['properties'] = properties
        _body = None
        rel_path = '/rest/api/3/project/recent'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def search_projects(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        id: Optional[list[int]] = None,
        keys: Optional[list[str]] = None,
        query: Optional[str] = None,
        typeKey: Optional[str] = None,
        categoryId: Optional[int] = None,
        action: Optional[str] = None,
        expand: Optional[str] = None,
        status: Optional[list[str]] = None,
        properties: Optional[list[Dict[str, Any]]] = None,
        propertyQuery: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get projects paginated\n\nHTTP GET /rest/api/3/project/search\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - orderBy (str, optional)\n  - id (list[int], optional)\n  - keys (list[str], optional)\n  - query (str, optional)\n  - typeKey (str, optional)\n  - categoryId (int, optional)\n  - action (str, optional)\n  - expand (str, optional)\n  - status (list[str], optional)\n  - properties (list[Dict[str, Any]], optional)\n  - propertyQuery (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if id is not None:
            _query['id'] = id
        if keys is not None:
            _query['keys'] = keys
        if query is not None:
            _query['query'] = query
        if typeKey is not None:
            _query['typeKey'] = typeKey
        if categoryId is not None:
            _query['categoryId'] = categoryId
        if action is not None:
            _query['action'] = action
        if expand is not None:
            _query['expand'] = expand
        if status is not None:
            _query['status'] = status
        if properties is not None:
            _query['properties'] = properties
        if propertyQuery is not None:
            _query['propertyQuery'] = propertyQuery
        _body = None
        rel_path = '/rest/api/3/project/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_project_types(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all project types\n\nHTTP GET /rest/api/3/project/type"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/type'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_accessible_project_types(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get licensed project types\n\nHTTP GET /rest/api/3/project/type/accessible"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/type/accessible'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_type_by_key(
        self,
        projectTypeKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project type by key\n\nHTTP GET /rest/api/3/project/type/{projectTypeKey}\nPath params:\n  - projectTypeKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectTypeKey': projectTypeKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/type/{projectTypeKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_accessible_project_type_by_key(
        self,
        projectTypeKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get accessible project type by key\n\nHTTP GET /rest/api/3/project/type/{projectTypeKey}/accessible\nPath params:\n  - projectTypeKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectTypeKey': projectTypeKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/type/{projectTypeKey}/accessible'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_project(
        self,
        projectIdOrKey: str,
        enableUndo: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete project\n\nHTTP DELETE /rest/api/3/project/{projectIdOrKey}\nPath params:\n  - projectIdOrKey (str)\nQuery params:\n  - enableUndo (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if enableUndo is not None:
            _query['enableUndo'] = enableUndo
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project(
        self,
        projectIdOrKey: str,
        expand: Optional[str] = None,
        properties: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}\nPath params:\n  - projectIdOrKey (str)\nQuery params:\n  - expand (str, optional)\n  - properties (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        if properties is not None:
            _query['properties'] = properties
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_project(
        self,
        projectIdOrKey: str,
        expand: Optional[str] = None,
        assigneeType: Optional[str] = None,
        avatarId: Optional[int] = None,
        categoryId: Optional[int] = None,
        description: Optional[str] = None,
        issueSecurityScheme: Optional[int] = None,
        key: Optional[str] = None,
        lead: Optional[str] = None,
        leadAccountId: Optional[str] = None,
        name: Optional[str] = None,
        notificationScheme: Optional[int] = None,
        permissionScheme: Optional[int] = None,
        releasedProjectKeys: Optional[list[str]] = None,
        url: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update project\n\nHTTP PUT /rest/api/3/project/{projectIdOrKey}\nPath params:\n  - projectIdOrKey (str)\nQuery params:\n  - expand (str, optional)\nBody (application/json) fields:\n  - assigneeType (str, optional)\n  - avatarId (int, optional)\n  - categoryId (int, optional)\n  - description (str, optional)\n  - issueSecurityScheme (int, optional)\n  - key (str, optional)\n  - lead (str, optional)\n  - leadAccountId (str, optional)\n  - name (str, optional)\n  - notificationScheme (int, optional)\n  - permissionScheme (int, optional)\n  - releasedProjectKeys (list[str], optional)\n  - url (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body: Dict[str, Any] = {}
        if assigneeType is not None:
            _body['assigneeType'] = assigneeType
        if avatarId is not None:
            _body['avatarId'] = avatarId
        if categoryId is not None:
            _body['categoryId'] = categoryId
        if description is not None:
            _body['description'] = description
        if issueSecurityScheme is not None:
            _body['issueSecurityScheme'] = issueSecurityScheme
        if key is not None:
            _body['key'] = key
        if lead is not None:
            _body['lead'] = lead
        if leadAccountId is not None:
            _body['leadAccountId'] = leadAccountId
        if name is not None:
            _body['name'] = name
        if notificationScheme is not None:
            _body['notificationScheme'] = notificationScheme
        if permissionScheme is not None:
            _body['permissionScheme'] = permissionScheme
        if releasedProjectKeys is not None:
            _body['releasedProjectKeys'] = releasedProjectKeys
        if url is not None:
            _body['url'] = url
        rel_path = '/rest/api/3/project/{projectIdOrKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def archive_project(
        self,
        projectIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Archive project\n\nHTTP POST /rest/api/3/project/{projectIdOrKey}/archive\nPath params:\n  - projectIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/archive'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_project_avatar(
        self,
        projectIdOrKey: str,
        id: str,
        fileName: Optional[str] = None,
        isDeletable: Optional[bool] = None,
        isSelected: Optional[bool] = None,
        isSystemAvatar: Optional[bool] = None,
        owner: Optional[str] = None,
        urls: Optional[Dict[str, Any]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set project avatar\n\nHTTP PUT /rest/api/3/project/{projectIdOrKey}/avatar\nPath params:\n  - projectIdOrKey (str)\nBody (application/json) fields:\n  - fileName (str, optional)\n  - id (str, required)\n  - isDeletable (bool, optional)\n  - isSelected (bool, optional)\n  - isSystemAvatar (bool, optional)\n  - owner (str, optional)\n  - urls (Dict[str, Any], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if fileName is not None:
            _body['fileName'] = fileName
        _body['id'] = id
        if isDeletable is not None:
            _body['isDeletable'] = isDeletable
        if isSelected is not None:
            _body['isSelected'] = isSelected
        if isSystemAvatar is not None:
            _body['isSystemAvatar'] = isSystemAvatar
        if owner is not None:
            _body['owner'] = owner
        if urls is not None:
            _body['urls'] = urls
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/project/{projectIdOrKey}/avatar'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_project_avatar(
        self,
        projectIdOrKey: str,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete project avatar\n\nHTTP DELETE /rest/api/3/project/{projectIdOrKey}/avatar/{id}\nPath params:\n  - projectIdOrKey (str)\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/avatar/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_project_avatar(
        self,
        projectIdOrKey: str,
        x: Optional[int] = None,
        y: Optional[int] = None,
        size: Optional[int] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Load project avatar\n\nHTTP POST /rest/api/3/project/{projectIdOrKey}/avatar2\nPath params:\n  - projectIdOrKey (str)\nQuery params:\n  - x (int, optional)\n  - y (int, optional)\n  - size (int, optional)\nBody: */* (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', '*/*')
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if x is not None:
            _query['x'] = x
        if y is not None:
            _query['y'] = y
        if size is not None:
            _query['size'] = size
        _body = body
        rel_path = '/rest/api/3/project/{projectIdOrKey}/avatar2'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_project_avatars(
        self,
        projectIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all project avatars\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/avatars\nPath params:\n  - projectIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/avatars'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_default_project_classification(
        self,
        projectIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove the default data classification level from a project\n\nHTTP DELETE /rest/api/3/project/{projectIdOrKey}/classification-level/default\nPath params:\n  - projectIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/classification-level/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_default_project_classification(
        self,
        projectIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get the default data classification level of a project\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/classification-level/default\nPath params:\n  - projectIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/classification-level/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_default_project_classification(
        self,
        projectIdOrKey: str,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update the default data classification level of a project\n\nHTTP PUT /rest/api/3/project/{projectIdOrKey}/classification-level/default\nPath params:\n  - projectIdOrKey (str)\nBody (application/json) fields:\n  - id (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['id'] = id
        rel_path = '/rest/api/3/project/{projectIdOrKey}/classification-level/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_components_paginated(
        self,
        projectIdOrKey: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        componentSource: Optional[str] = None,
        query: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project components paginated\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/component\nPath params:\n  - projectIdOrKey (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - orderBy (str, optional)\n  - componentSource (str, optional)\n  - query (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if componentSource is not None:
            _query['componentSource'] = componentSource
        if query is not None:
            _query['query'] = query
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/component'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_components(
        self,
        projectIdOrKey: str,
        componentSource: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project components\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/components\nPath params:\n  - projectIdOrKey (str)\nQuery params:\n  - componentSource (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if componentSource is not None:
            _query['componentSource'] = componentSource
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/components'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_project_asynchronously(
        self,
        projectIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete project asynchronously\n\nHTTP POST /rest/api/3/project/{projectIdOrKey}/delete\nPath params:\n  - projectIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/delete'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_features_for_project(
        self,
        projectIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project features\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/features\nPath params:\n  - projectIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/features'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def toggle_feature_for_project(
        self,
        projectIdOrKey: str,
        featureKey: str,
        state: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set project feature state\n\nHTTP PUT /rest/api/3/project/{projectIdOrKey}/features/{featureKey}\nPath params:\n  - projectIdOrKey (str)\n  - featureKey (str)\nBody (application/json) fields:\n  - state (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
            'featureKey': featureKey,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if state is not None:
            _body['state'] = state
        rel_path = '/rest/api/3/project/{projectIdOrKey}/features/{featureKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_property_keys(
        self,
        projectIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project property keys\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/properties\nPath params:\n  - projectIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_project_property(
        self,
        projectIdOrKey: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete project property\n\nHTTP DELETE /rest/api/3/project/{projectIdOrKey}/properties/{propertyKey}\nPath params:\n  - projectIdOrKey (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_property(
        self,
        projectIdOrKey: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project property\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/properties/{propertyKey}\nPath params:\n  - projectIdOrKey (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_project_property(
        self,
        projectIdOrKey: str,
        propertyKey: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set project property\n\nHTTP PUT /rest/api/3/project/{projectIdOrKey}/properties/{propertyKey}\nPath params:\n  - projectIdOrKey (str)\n  - propertyKey (str)\nBody: application/json (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/api/3/project/{projectIdOrKey}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def restore(
        self,
        projectIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Restore deleted or archived project\n\nHTTP POST /rest/api/3/project/{projectIdOrKey}/restore\nPath params:\n  - projectIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/restore'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_roles(
        self,
        projectIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project roles for project\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/role\nPath params:\n  - projectIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/role'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_actor(
        self,
        projectIdOrKey: str,
        id: int,
        user: Optional[str] = None,
        group: Optional[str] = None,
        groupId: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete actors from project role\n\nHTTP DELETE /rest/api/3/project/{projectIdOrKey}/role/{id}\nPath params:\n  - projectIdOrKey (str)\n  - id (int)\nQuery params:\n  - user (str, optional)\n  - group (str, optional)\n  - groupId (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if user is not None:
            _query['user'] = user
        if group is not None:
            _query['group'] = group
        if groupId is not None:
            _query['groupId'] = groupId
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/role/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_role(
        self,
        projectIdOrKey: str,
        id: int,
        excludeInactiveUsers: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project role for project\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/role/{id}\nPath params:\n  - projectIdOrKey (str)\n  - id (int)\nQuery params:\n  - excludeInactiveUsers (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if excludeInactiveUsers is not None:
            _query['excludeInactiveUsers'] = excludeInactiveUsers
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/role/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_actor_users(
        self,
        projectIdOrKey: str,
        id: int,
        group: Optional[list[str]] = None,
        groupId: Optional[list[str]] = None,
        user: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add actors to project role\n\nHTTP POST /rest/api/3/project/{projectIdOrKey}/role/{id}\nPath params:\n  - projectIdOrKey (str)\n  - id (int)\nBody (application/json) fields:\n  - group (list[str], optional)\n  - groupId (list[str], optional)\n  - user (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if group is not None:
            _body['group'] = group
        if groupId is not None:
            _body['groupId'] = groupId
        if user is not None:
            _body['user'] = user
        rel_path = '/rest/api/3/project/{projectIdOrKey}/role/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_actors(
        self,
        projectIdOrKey: str,
        id: int,
        categorisedActors: Optional[Dict[str, Any]] = None,
        id_body: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set actors for project role\n\nHTTP PUT /rest/api/3/project/{projectIdOrKey}/role/{id}\nPath params:\n  - projectIdOrKey (str)\n  - id (int)\nBody (application/json) fields:\n  - categorisedActors (Dict[str, Any], optional)\n  - id (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if categorisedActors is not None:
            _body['categorisedActors'] = categorisedActors
        if id_body is not None:
            _body['id'] = id_body
        rel_path = '/rest/api/3/project/{projectIdOrKey}/role/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_role_details(
        self,
        projectIdOrKey: str,
        currentMember: Optional[bool] = None,
        excludeConnectAddons: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project role details\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/roledetails\nPath params:\n  - projectIdOrKey (str)\nQuery params:\n  - currentMember (bool, optional)\n  - excludeConnectAddons (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if currentMember is not None:
            _query['currentMember'] = currentMember
        if excludeConnectAddons is not None:
            _query['excludeConnectAddons'] = excludeConnectAddons
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/roledetails'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_statuses(
        self,
        projectIdOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all statuses for project\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/statuses\nPath params:\n  - projectIdOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/statuses'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_versions_paginated(
        self,
        projectIdOrKey: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        orderBy: Optional[str] = None,
        query: Optional[str] = None,
        status: Optional[str] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project versions paginated\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/version\nPath params:\n  - projectIdOrKey (str)\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - orderBy (str, optional)\n  - query (str, optional)\n  - status (str, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if query is not None:
            _query['query'] = query
        if status is not None:
            _query['status'] = status
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/version'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_versions(
        self,
        projectIdOrKey: str,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project versions\n\nHTTP GET /rest/api/3/project/{projectIdOrKey}/versions\nPath params:\n  - projectIdOrKey (str)\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectIdOrKey': projectIdOrKey,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/project/{projectIdOrKey}/versions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_email(
        self,
        projectId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project's sender email\n\nHTTP GET /rest/api/3/project/{projectId}/email\nPath params:\n  - projectId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectId': projectId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectId}/email'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_project_email(
        self,
        projectId: int,
        emailAddress: Optional[str] = None,
        emailAddressStatus: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set project's sender email\n\nHTTP PUT /rest/api/3/project/{projectId}/email\nPath params:\n  - projectId (int)\nBody (application/json) fields:\n  - emailAddress (str, optional)\n  - emailAddressStatus (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'projectId': projectId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if emailAddress is not None:
            _body['emailAddress'] = emailAddress
        if emailAddressStatus is not None:
            _body['emailAddressStatus'] = emailAddressStatus
        rel_path = '/rest/api/3/project/{projectId}/email'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_hierarchy(
        self,
        projectId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project issue type hierarchy\n\nHTTP GET /rest/api/3/project/{projectId}/hierarchy\nPath params:\n  - projectId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectId': projectId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectId}/hierarchy'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_issue_security_scheme(
        self,
        projectKeyOrId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project issue security scheme\n\nHTTP GET /rest/api/3/project/{projectKeyOrId}/issuesecuritylevelscheme\nPath params:\n  - projectKeyOrId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectKeyOrId': projectKeyOrId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectKeyOrId}/issuesecuritylevelscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_notification_scheme_for_project(
        self,
        projectKeyOrId: str,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project notification scheme\n\nHTTP GET /rest/api/3/project/{projectKeyOrId}/notificationscheme\nPath params:\n  - projectKeyOrId (str)\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectKeyOrId': projectKeyOrId,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/project/{projectKeyOrId}/notificationscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_assigned_permission_scheme(
        self,
        projectKeyOrId: str,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get assigned permission scheme\n\nHTTP GET /rest/api/3/project/{projectKeyOrId}/permissionscheme\nPath params:\n  - projectKeyOrId (str)\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectKeyOrId': projectKeyOrId,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/project/{projectKeyOrId}/permissionscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def assign_permission_scheme(
        self,
        projectKeyOrId: str,
        id: int,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Assign permission scheme\n\nHTTP PUT /rest/api/3/project/{projectKeyOrId}/permissionscheme\nPath params:\n  - projectKeyOrId (str)\nQuery params:\n  - expand (str, optional)\nBody (application/json) fields:\n  - id (int, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'projectKeyOrId': projectKeyOrId,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body: Dict[str, Any] = {}
        _body['id'] = id
        rel_path = '/rest/api/3/project/{projectKeyOrId}/permissionscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_security_levels_for_project(
        self,
        projectKeyOrId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project issue security levels\n\nHTTP GET /rest/api/3/project/{projectKeyOrId}/securitylevel\nPath params:\n  - projectKeyOrId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'projectKeyOrId': projectKeyOrId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/project/{projectKeyOrId}/securitylevel'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_project_categories(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all project categories\n\nHTTP GET /rest/api/3/projectCategory"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/projectCategory'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_project_category(
        self,
        description: Optional[str] = None,
        id: Optional[str] = None,
        name: Optional[str] = None,
        self_: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create project category\n\nHTTP POST /rest/api/3/projectCategory\nBody (application/json) fields:\n  - description (str, optional)\n  - id (str, optional)\n  - name (str, optional)\n  - self (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if id is not None:
            _body['id'] = id
        if name is not None:
            _body['name'] = name
        if self_ is not None:
            _body['self'] = self_
        rel_path = '/rest/api/3/projectCategory'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_project_category(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete project category\n\nHTTP DELETE /rest/api/3/projectCategory/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/projectCategory/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_category_by_id(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project category by ID\n\nHTTP GET /rest/api/3/projectCategory/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/projectCategory/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_project_category(
        self,
        id: int,
        description: Optional[str] = None,
        id_body: Optional[str] = None,
        name: Optional[str] = None,
        self_: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update project category\n\nHTTP PUT /rest/api/3/projectCategory/{id}\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - description (str, optional)\n  - id (str, optional)\n  - name (str, optional)\n  - self (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if id_body is not None:
            _body['id'] = id_body
        if name is not None:
            _body['name'] = name
        if self_ is not None:
            _body['self'] = self_
        rel_path = '/rest/api/3/projectCategory/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def validate_project_key(
        self,
        key: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Validate project key\n\nHTTP GET /rest/api/3/projectvalidate/key\nQuery params:\n  - key (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        _body = None
        rel_path = '/rest/api/3/projectvalidate/key'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_valid_project_key(
        self,
        key: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get valid project key\n\nHTTP GET /rest/api/3/projectvalidate/validProjectKey\nQuery params:\n  - key (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        _body = None
        rel_path = '/rest/api/3/projectvalidate/validProjectKey'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_valid_project_name(
        self,
        name: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get valid project name\n\nHTTP GET /rest/api/3/projectvalidate/validProjectName\nQuery params:\n  - name (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['name'] = name
        _body = None
        rel_path = '/rest/api/3/projectvalidate/validProjectName'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def redact(
        self,
        redactions: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Redact\n\nHTTP POST /rest/api/3/redact\nBody (application/json) fields:\n  - redactions (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if redactions is not None:
            _body['redactions'] = redactions
        rel_path = '/rest/api/3/redact'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_redaction_status(
        self,
        jobId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get redaction status\n\nHTTP GET /rest/api/3/redact/status/{jobId}\nPath params:\n  - jobId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'jobId': jobId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/redact/status/{jobId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_resolutions(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get resolutions\n\nHTTP GET /rest/api/3/resolution"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/resolution'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_resolution(
        self,
        name: str,
        description: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create resolution\n\nHTTP POST /rest/api/3/resolution\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, required)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['name'] = name
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/resolution'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_default_resolution(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set default resolution\n\nHTTP PUT /rest/api/3/resolution/default\nBody (application/json) fields:\n  - id (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['id'] = id
        rel_path = '/rest/api/3/resolution/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def move_resolutions(
        self,
        ids: list[str],
        after: Optional[str] = None,
        position: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Move resolutions\n\nHTTP PUT /rest/api/3/resolution/move\nBody (application/json) fields:\n  - after (str, optional)\n  - ids (list[str], required)\n  - position (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if after is not None:
            _body['after'] = after
        _body['ids'] = ids
        if position is not None:
            _body['position'] = position
        rel_path = '/rest/api/3/resolution/move'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def search_resolutions(
        self,
        startAt: Optional[str] = None,
        maxResults: Optional[str] = None,
        id: Optional[list[str]] = None,
        onlyDefault: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Search resolutions\n\nHTTP GET /rest/api/3/resolution/search\nQuery params:\n  - startAt (str, optional)\n  - maxResults (str, optional)\n  - id (list[str], optional)\n  - onlyDefault (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        if onlyDefault is not None:
            _query['onlyDefault'] = onlyDefault
        _body = None
        rel_path = '/rest/api/3/resolution/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_resolution(
        self,
        id: str,
        replaceWith: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete resolution\n\nHTTP DELETE /rest/api/3/resolution/{id}\nPath params:\n  - id (str)\nQuery params:\n  - replaceWith (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _query['replaceWith'] = replaceWith
        _body = None
        rel_path = '/rest/api/3/resolution/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_resolution(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get resolution\n\nHTTP GET /rest/api/3/resolution/{id}\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/resolution/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_resolution(
        self,
        id: str,
        name: str,
        description: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update resolution\n\nHTTP PUT /rest/api/3/resolution/{id}\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, required)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['name'] = name
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/resolution/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_project_roles(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all project roles\n\nHTTP GET /rest/api/3/role"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/role'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_project_role(
        self,
        description: Optional[str] = None,
        name: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create project role\n\nHTTP POST /rest/api/3/role\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        rel_path = '/rest/api/3/role'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_project_role(
        self,
        id: int,
        swap: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete project role\n\nHTTP DELETE /rest/api/3/role/{id}\nPath params:\n  - id (int)\nQuery params:\n  - swap (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if swap is not None:
            _query['swap'] = swap
        _body = None
        rel_path = '/rest/api/3/role/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_role_by_id(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project role by ID\n\nHTTP GET /rest/api/3/role/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/role/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def partial_update_project_role(
        self,
        id: int,
        description: Optional[str] = None,
        name: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Partial update project role\n\nHTTP POST /rest/api/3/role/{id}\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        rel_path = '/rest/api/3/role/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def fully_update_project_role(
        self,
        id: int,
        description: Optional[str] = None,
        name: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Fully update project role\n\nHTTP PUT /rest/api/3/role/{id}\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        rel_path = '/rest/api/3/role/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_project_role_actors_from_role(
        self,
        id: int,
        user: Optional[str] = None,
        groupId: Optional[str] = None,
        group: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete default actors from project role\n\nHTTP DELETE /rest/api/3/role/{id}/actors\nPath params:\n  - id (int)\nQuery params:\n  - user (str, optional)\n  - groupId (str, optional)\n  - group (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if user is not None:
            _query['user'] = user
        if groupId is not None:
            _query['groupId'] = groupId
        if group is not None:
            _query['group'] = group
        _body = None
        rel_path = '/rest/api/3/role/{id}/actors'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_role_actors_for_role(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get default actors for project role\n\nHTTP GET /rest/api/3/role/{id}/actors\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/role/{id}/actors'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_project_role_actors_to_role(
        self,
        id: int,
        group: Optional[list[str]] = None,
        groupId: Optional[list[str]] = None,
        user: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add default actors to project role\n\nHTTP POST /rest/api/3/role/{id}/actors\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - group (list[str], optional)\n  - groupId (list[str], optional)\n  - user (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if group is not None:
            _body['group'] = group
        if groupId is not None:
            _body['groupId'] = groupId
        if user is not None:
            _body['user'] = user
        rel_path = '/rest/api/3/role/{id}/actors'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_screens(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        id: Optional[list[int]] = None,
        queryString: Optional[str] = None,
        scope: Optional[list[str]] = None,
        orderBy: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get screens\n\nHTTP GET /rest/api/3/screens\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - id (list[int], optional)\n  - queryString (str, optional)\n  - scope (list[str], optional)\n  - orderBy (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        if queryString is not None:
            _query['queryString'] = queryString
        if scope is not None:
            _query['scope'] = scope
        if orderBy is not None:
            _query['orderBy'] = orderBy
        _body = None
        rel_path = '/rest/api/3/screens'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_screen(
        self,
        name: str,
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create screen\n\nHTTP POST /rest/api/3/screens\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['name'] = name
        rel_path = '/rest/api/3/screens'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_field_to_default_screen(
        self,
        fieldId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add field to default screen\n\nHTTP POST /rest/api/3/screens/addToDefault/{fieldId}\nPath params:\n  - fieldId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'fieldId': fieldId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/screens/addToDefault/{fieldId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_bulk_screen_tabs(
        self,
        screenId: Optional[list[int]] = None,
        tabId: Optional[list[int]] = None,
        startAt: Optional[int] = None,
        maxResult: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get bulk screen tabs\n\nHTTP GET /rest/api/3/screens/tabs\nQuery params:\n  - screenId (list[int], optional)\n  - tabId (list[int], optional)\n  - startAt (int, optional)\n  - maxResult (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if screenId is not None:
            _query['screenId'] = screenId
        if tabId is not None:
            _query['tabId'] = tabId
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResult is not None:
            _query['maxResult'] = maxResult
        _body = None
        rel_path = '/rest/api/3/screens/tabs'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_screen(
        self,
        screenId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete screen\n\nHTTP DELETE /rest/api/3/screens/{screenId}\nPath params:\n  - screenId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'screenId': screenId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/screens/{screenId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_screen(
        self,
        screenId: int,
        description: Optional[str] = None,
        name: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update screen\n\nHTTP PUT /rest/api/3/screens/{screenId}\nPath params:\n  - screenId (int)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'screenId': screenId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        rel_path = '/rest/api/3/screens/{screenId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_available_screen_fields(
        self,
        screenId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get available screen fields\n\nHTTP GET /rest/api/3/screens/{screenId}/availableFields\nPath params:\n  - screenId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'screenId': screenId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/screens/{screenId}/availableFields'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_screen_tabs(
        self,
        screenId: int,
        projectKey: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all screen tabs\n\nHTTP GET /rest/api/3/screens/{screenId}/tabs\nPath params:\n  - screenId (int)\nQuery params:\n  - projectKey (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'screenId': screenId,
        }
        _query: Dict[str, Any] = {}
        if projectKey is not None:
            _query['projectKey'] = projectKey
        _body = None
        rel_path = '/rest/api/3/screens/{screenId}/tabs'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_screen_tab(
        self,
        screenId: int,
        name: str,
        id: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create screen tab\n\nHTTP POST /rest/api/3/screens/{screenId}/tabs\nPath params:\n  - screenId (int)\nBody (application/json) fields:\n  - id (int, optional)\n  - name (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'screenId': screenId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if id is not None:
            _body['id'] = id
        _body['name'] = name
        rel_path = '/rest/api/3/screens/{screenId}/tabs'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_screen_tab(
        self,
        screenId: int,
        tabId: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete screen tab\n\nHTTP DELETE /rest/api/3/screens/{screenId}/tabs/{tabId}\nPath params:\n  - screenId (int)\n  - tabId (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'screenId': screenId,
            'tabId': tabId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/screens/{screenId}/tabs/{tabId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def rename_screen_tab(
        self,
        screenId: int,
        tabId: int,
        name: str,
        id: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update screen tab\n\nHTTP PUT /rest/api/3/screens/{screenId}/tabs/{tabId}\nPath params:\n  - screenId (int)\n  - tabId (int)\nBody (application/json) fields:\n  - id (int, optional)\n  - name (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'screenId': screenId,
            'tabId': tabId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if id is not None:
            _body['id'] = id
        _body['name'] = name
        rel_path = '/rest/api/3/screens/{screenId}/tabs/{tabId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_screen_tab_fields(
        self,
        screenId: int,
        tabId: int,
        projectKey: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all screen tab fields\n\nHTTP GET /rest/api/3/screens/{screenId}/tabs/{tabId}/fields\nPath params:\n  - screenId (int)\n  - tabId (int)\nQuery params:\n  - projectKey (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'screenId': screenId,
            'tabId': tabId,
        }
        _query: Dict[str, Any] = {}
        if projectKey is not None:
            _query['projectKey'] = projectKey
        _body = None
        rel_path = '/rest/api/3/screens/{screenId}/tabs/{tabId}/fields'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def add_screen_tab_field(
        self,
        screenId: int,
        tabId: int,
        fieldId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add screen tab field\n\nHTTP POST /rest/api/3/screens/{screenId}/tabs/{tabId}/fields\nPath params:\n  - screenId (int)\n  - tabId (int)\nBody (application/json) fields:\n  - fieldId (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'screenId': screenId,
            'tabId': tabId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['fieldId'] = fieldId
        rel_path = '/rest/api/3/screens/{screenId}/tabs/{tabId}/fields'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_screen_tab_field(
        self,
        screenId: int,
        tabId: int,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove screen tab field\n\nHTTP DELETE /rest/api/3/screens/{screenId}/tabs/{tabId}/fields/{id}\nPath params:\n  - screenId (int)\n  - tabId (int)\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'screenId': screenId,
            'tabId': tabId,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/screens/{screenId}/tabs/{tabId}/fields/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def move_screen_tab_field(
        self,
        screenId: int,
        tabId: int,
        id: str,
        after: Optional[str] = None,
        position: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Move screen tab field\n\nHTTP POST /rest/api/3/screens/{screenId}/tabs/{tabId}/fields/{id}/move\nPath params:\n  - screenId (int)\n  - tabId (int)\n  - id (str)\nBody (application/json) fields:\n  - after (str, optional)\n  - position (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'screenId': screenId,
            'tabId': tabId,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if after is not None:
            _body['after'] = after
        if position is not None:
            _body['position'] = position
        rel_path = '/rest/api/3/screens/{screenId}/tabs/{tabId}/fields/{id}/move'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def move_screen_tab(
        self,
        screenId: int,
        tabId: int,
        pos: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Move screen tab\n\nHTTP POST /rest/api/3/screens/{screenId}/tabs/{tabId}/move/{pos}\nPath params:\n  - screenId (int)\n  - tabId (int)\n  - pos (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'screenId': screenId,
            'tabId': tabId,
            'pos': pos,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/screens/{screenId}/tabs/{tabId}/move/{pos}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_screen_schemes(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        id: Optional[list[int]] = None,
        expand: Optional[str] = None,
        queryString: Optional[str] = None,
        orderBy: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get screen schemes\n\nHTTP GET /rest/api/3/screenscheme\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - id (list[int], optional)\n  - expand (str, optional)\n  - queryString (str, optional)\n  - orderBy (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if id is not None:
            _query['id'] = id
        if expand is not None:
            _query['expand'] = expand
        if queryString is not None:
            _query['queryString'] = queryString
        if orderBy is not None:
            _query['orderBy'] = orderBy
        _body = None
        rel_path = '/rest/api/3/screenscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_screen_scheme(
        self,
        name: str,
        screens: Dict[str, Any],
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create screen scheme\n\nHTTP POST /rest/api/3/screenscheme\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, required)\n  - screens (Dict[str, Any], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['name'] = name
        _body['screens'] = screens
        rel_path = '/rest/api/3/screenscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_screen_scheme(
        self,
        screenSchemeId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete screen scheme\n\nHTTP DELETE /rest/api/3/screenscheme/{screenSchemeId}\nPath params:\n  - screenSchemeId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'screenSchemeId': screenSchemeId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/screenscheme/{screenSchemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_screen_scheme(
        self,
        screenSchemeId: str,
        description: Optional[str] = None,
        name: Optional[str] = None,
        screens: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update screen scheme\n\nHTTP PUT /rest/api/3/screenscheme/{screenSchemeId}\nPath params:\n  - screenSchemeId (str)\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, optional)\n  - screens (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'screenSchemeId': screenSchemeId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        if screens is not None:
            _body['screens'] = screens
        rel_path = '/rest/api/3/screenscheme/{screenSchemeId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def search_for_issues_using_jql(
        self,
        jql: Optional[str] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        validateQuery: Optional[str] = None,
        fields: Optional[list[str]] = None,
        expand: Optional[str] = None,
        properties: Optional[list[str]] = None,
        fieldsByKeys: Optional[bool] = None,
        failFast: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Currently being removed. Search for issues using JQL (GET)\n\nHTTP GET /rest/api/3/search\nQuery params:\n  - jql (str, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - validateQuery (str, optional)\n  - fields (list[str], optional)\n  - expand (str, optional)\n  - properties (list[str], optional)\n  - fieldsByKeys (bool, optional)\n  - failFast (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if jql is not None:
            _query['jql'] = jql
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if validateQuery is not None:
            _query['validateQuery'] = validateQuery
        if fields is not None:
            _query['fields'] = fields
        if expand is not None:
            _query['expand'] = expand
        if properties is not None:
            _query['properties'] = properties
        if fieldsByKeys is not None:
            _query['fieldsByKeys'] = fieldsByKeys
        if failFast is not None:
            _query['failFast'] = failFast
        _body = None
        rel_path = '/rest/api/3/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def search_for_issues_using_jql_post(
        self,
        expand: Optional[list[str]] = None,
        fields: Optional[list[str]] = None,
        fieldsByKeys: Optional[bool] = None,
        jql: Optional[str] = None,
        maxResults: Optional[int] = None,
        properties: Optional[list[str]] = None,
        startAt: Optional[int] = None,
        validateQuery: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Currently being removed. Search for issues using JQL (POST)\n\nHTTP POST /rest/api/3/search\nBody (application/json) fields:\n  - expand (list[str], optional)\n  - fields (list[str], optional)\n  - fieldsByKeys (bool, optional)\n  - jql (str, optional)\n  - maxResults (int, optional)\n  - properties (list[str], optional)\n  - startAt (int, optional)\n  - validateQuery (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if expand is not None:
            _body['expand'] = expand
        if fields is not None:
            _body['fields'] = fields
        if fieldsByKeys is not None:
            _body['fieldsByKeys'] = fieldsByKeys
        if jql is not None:
            _body['jql'] = jql
        if maxResults is not None:
            _body['maxResults'] = maxResults
        if properties is not None:
            _body['properties'] = properties
        if startAt is not None:
            _body['startAt'] = startAt
        if validateQuery is not None:
            _body['validateQuery'] = validateQuery
        rel_path = '/rest/api/3/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def count_issues(
        self,
        jql: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Count issues using JQL\n\nHTTP POST /rest/api/3/search/approximate-count\nBody (application/json) fields:\n  - jql (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if jql is not None:
            _body['jql'] = jql
        rel_path = '/rest/api/3/search/approximate-count'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def search_and_reconsile_issues_using_jql(
        self,
        jql: Optional[str] = None,
        nextPageToken: Optional[str] = None,
        maxResults: Optional[int] = None,
        fields: Optional[list[str]] = None,
        expand: Optional[str] = None,
        properties: Optional[list[str]] = None,
        fieldsByKeys: Optional[bool] = None,
        failFast: Optional[bool] = None,
        reconcileIssues: Optional[list[int]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Search for issues using JQL enhanced search (GET)\n\nHTTP GET /rest/api/3/search/jql\nQuery params:\n  - jql (str, optional)\n  - nextPageToken (str, optional)\n  - maxResults (int, optional)\n  - fields (list[str], optional)\n  - expand (str, optional)\n  - properties (list[str], optional)\n  - fieldsByKeys (bool, optional)\n  - failFast (bool, optional)\n  - reconcileIssues (list[int], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if jql is not None:
            _query['jql'] = jql
        if nextPageToken is not None:
            _query['nextPageToken'] = nextPageToken
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if fields is not None:
            _query['fields'] = fields
        if expand is not None:
            _query['expand'] = expand
        if properties is not None:
            _query['properties'] = properties
        if fieldsByKeys is not None:
            _query['fieldsByKeys'] = fieldsByKeys
        if failFast is not None:
            _query['failFast'] = failFast
        if reconcileIssues is not None:
            _query['reconcileIssues'] = reconcileIssues
        _body = None
        rel_path = '/rest/api/3/search/jql'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def search_and_reconsile_issues_using_jql_post(
        self,
        expand: Optional[str] = None,
        fields: Optional[list[str]] = None,
        fieldsByKeys: Optional[bool] = None,
        jql: Optional[str] = None,
        maxResults: Optional[int] = None,
        nextPageToken: Optional[str] = None,
        properties: Optional[list[str]] = None,
        reconcileIssues: Optional[list[int]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Search for issues using JQL enhanced search (POST)\n\nHTTP POST /rest/api/3/search/jql\nBody (application/json) fields:\n  - expand (str, optional)\n  - fields (list[str], optional)\n  - fieldsByKeys (bool, optional)\n  - jql (str, optional)\n  - maxResults (int, optional)\n  - nextPageToken (str, optional)\n  - properties (list[str], optional)\n  - reconcileIssues (list[int], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if expand is not None:
            _body['expand'] = expand
        if fields is not None:
            _body['fields'] = fields
        if fieldsByKeys is not None:
            _body['fieldsByKeys'] = fieldsByKeys
        if jql is not None:
            _body['jql'] = jql
        if maxResults is not None:
            _body['maxResults'] = maxResults
        if nextPageToken is not None:
            _body['nextPageToken'] = nextPageToken
        if properties is not None:
            _body['properties'] = properties
        if reconcileIssues is not None:
            _body['reconcileIssues'] = reconcileIssues
        rel_path = '/rest/api/3/search/jql'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_security_level(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue security level\n\nHTTP GET /rest/api/3/securitylevel/{id}\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/securitylevel/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_server_info(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Jira instance info\n\nHTTP GET /rest/api/3/serverInfo"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/serverInfo'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_issue_navigator_default_columns(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue navigator default columns\n\nHTTP GET /rest/api/3/settings/columns"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/settings/columns'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_issue_navigator_default_columns(
        self,
        columns: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set issue navigator default columns\n\nHTTP PUT /rest/api/3/settings/columns\nBody (multipart/form-data) fields:\n  - columns (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'multipart/form-data')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if columns is not None:
            _body['columns'] = columns
        rel_path = '/rest/api/3/settings/columns'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_statuses(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all statuses\n\nHTTP GET /rest/api/3/status"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/status'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_status(
        self,
        idOrName: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get status\n\nHTTP GET /rest/api/3/status/{idOrName}\nPath params:\n  - idOrName (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'idOrName': idOrName,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/status/{idOrName}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_status_categories(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all status categories\n\nHTTP GET /rest/api/3/statuscategory"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/statuscategory'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_status_category(
        self,
        idOrKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get status category\n\nHTTP GET /rest/api/3/statuscategory/{idOrKey}\nPath params:\n  - idOrKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'idOrKey': idOrKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/statuscategory/{idOrKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_statuses_by_id(
        self,
        id: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk delete Statuses\n\nHTTP DELETE /rest/api/3/statuses\nQuery params:\n  - id (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['id'] = id
        _body = None
        rel_path = '/rest/api/3/statuses'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_statuses_by_id(
        self,
        id: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk get statuses\n\nHTTP GET /rest/api/3/statuses\nQuery params:\n  - id (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['id'] = id
        _body = None
        rel_path = '/rest/api/3/statuses'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_statuses(
        self,
        scope: Dict[str, Any],
        statuses: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk create statuses\n\nHTTP POST /rest/api/3/statuses\nBody (application/json) fields:\n  - scope (Dict[str, Any], required)\n  - statuses (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['scope'] = scope
        _body['statuses'] = statuses
        rel_path = '/rest/api/3/statuses'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_statuses(
        self,
        statuses: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk update statuses\n\nHTTP PUT /rest/api/3/statuses\nBody (application/json) fields:\n  - statuses (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['statuses'] = statuses
        rel_path = '/rest/api/3/statuses'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def search(
        self,
        projectId: Optional[str] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        searchString: Optional[str] = None,
        statusCategory: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Search statuses paginated\n\nHTTP GET /rest/api/3/statuses/search\nQuery params:\n  - projectId (str, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - searchString (str, optional)\n  - statusCategory (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if projectId is not None:
            _query['projectId'] = projectId
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if searchString is not None:
            _query['searchString'] = searchString
        if statusCategory is not None:
            _query['statusCategory'] = statusCategory
        _body = None
        rel_path = '/rest/api/3/statuses/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_issue_type_usages_for_status(
        self,
        statusId: str,
        projectId: str,
        nextPageToken: Optional[str] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue type usages by status and project\n\nHTTP GET /rest/api/3/statuses/{statusId}/project/{projectId}/issueTypeUsages\nPath params:\n  - statusId (str)\n  - projectId (str)\nQuery params:\n  - nextPageToken (str, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'statusId': statusId,
            'projectId': projectId,
        }
        _query: Dict[str, Any] = {}
        if nextPageToken is not None:
            _query['nextPageToken'] = nextPageToken
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/statuses/{statusId}/project/{projectId}/issueTypeUsages'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_usages_for_status(
        self,
        statusId: str,
        nextPageToken: Optional[str] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get project usages by status\n\nHTTP GET /rest/api/3/statuses/{statusId}/projectUsages\nPath params:\n  - statusId (str)\nQuery params:\n  - nextPageToken (str, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'statusId': statusId,
        }
        _query: Dict[str, Any] = {}
        if nextPageToken is not None:
            _query['nextPageToken'] = nextPageToken
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/statuses/{statusId}/projectUsages'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workflow_usages_for_status(
        self,
        statusId: str,
        nextPageToken: Optional[str] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get workflow usages by status\n\nHTTP GET /rest/api/3/statuses/{statusId}/workflowUsages\nPath params:\n  - statusId (str)\nQuery params:\n  - nextPageToken (str, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'statusId': statusId,
        }
        _query: Dict[str, Any] = {}
        if nextPageToken is not None:
            _query['nextPageToken'] = nextPageToken
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/statuses/{statusId}/workflowUsages'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_task(
        self,
        taskId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get task\n\nHTTP GET /rest/api/3/task/{taskId}\nPath params:\n  - taskId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'taskId': taskId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/task/{taskId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def cancel_task(
        self,
        taskId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Cancel task\n\nHTTP POST /rest/api/3/task/{taskId}/cancel\nPath params:\n  - taskId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'taskId': taskId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/task/{taskId}/cancel'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_ui_modifications(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get UI modifications\n\nHTTP GET /rest/api/3/uiModifications\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/uiModifications'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_ui_modification(
        self,
        name: str,
        contexts: Optional[list[Dict[str, Any]]] = None,
        data: Optional[str] = None,
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create UI modification\n\nHTTP POST /rest/api/3/uiModifications\nBody (application/json) fields:\n  - contexts (list[Dict[str, Any]], optional)\n  - data (str, optional)\n  - description (str, optional)\n  - name (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if contexts is not None:
            _body['contexts'] = contexts
        if data is not None:
            _body['data'] = data
        if description is not None:
            _body['description'] = description
        _body['name'] = name
        rel_path = '/rest/api/3/uiModifications'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_ui_modification(
        self,
        uiModificationId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete UI modification\n\nHTTP DELETE /rest/api/3/uiModifications/{uiModificationId}\nPath params:\n  - uiModificationId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'uiModificationId': uiModificationId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/uiModifications/{uiModificationId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_ui_modification(
        self,
        uiModificationId: str,
        contexts: Optional[list[Dict[str, Any]]] = None,
        data: Optional[str] = None,
        description: Optional[str] = None,
        name: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update UI modification\n\nHTTP PUT /rest/api/3/uiModifications/{uiModificationId}\nPath params:\n  - uiModificationId (str)\nBody (application/json) fields:\n  - contexts (list[Dict[str, Any]], optional)\n  - data (str, optional)\n  - description (str, optional)\n  - name (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'uiModificationId': uiModificationId,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if contexts is not None:
            _body['contexts'] = contexts
        if data is not None:
            _body['data'] = data
        if description is not None:
            _body['description'] = description
        if name is not None:
            _body['name'] = name
        rel_path = '/rest/api/3/uiModifications/{uiModificationId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_avatars(
        self,
        type: str,
        entityId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get avatars\n\nHTTP GET /rest/api/3/universal_avatar/type/{type}/owner/{entityId}\nPath params:\n  - type (str)\n  - entityId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'type': type,
            'entityId': entityId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/universal_avatar/type/{type}/owner/{entityId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def store_avatar(
        self,
        type: str,
        entityId: str,
        size: int,
        x: Optional[int] = None,
        y: Optional[int] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Load avatar\n\nHTTP POST /rest/api/3/universal_avatar/type/{type}/owner/{entityId}\nPath params:\n  - type (str)\n  - entityId (str)\nQuery params:\n  - x (int, optional)\n  - y (int, optional)\n  - size (int, required)\nBody: */* (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', '*/*')
        _path: Dict[str, Any] = {
            'type': type,
            'entityId': entityId,
        }
        _query: Dict[str, Any] = {}
        if x is not None:
            _query['x'] = x
        if y is not None:
            _query['y'] = y
        _query['size'] = size
        _body = body
        rel_path = '/rest/api/3/universal_avatar/type/{type}/owner/{entityId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_avatar(
        self,
        type: str,
        owningObjectId: str,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete avatar\n\nHTTP DELETE /rest/api/3/universal_avatar/type/{type}/owner/{owningObjectId}/avatar/{id}\nPath params:\n  - type (str)\n  - owningObjectId (str)\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'type': type,
            'owningObjectId': owningObjectId,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/universal_avatar/type/{type}/owner/{owningObjectId}/avatar/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_avatar_image_by_type(
        self,
        type: str,
        size: Optional[str] = None,
        format: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get avatar image by type\n\nHTTP GET /rest/api/3/universal_avatar/view/type/{type}\nPath params:\n  - type (str)\nQuery params:\n  - size (str, optional)\n  - format (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'type': type,
        }
        _query: Dict[str, Any] = {}
        if size is not None:
            _query['size'] = size
        if format is not None:
            _query['format'] = format
        _body = None
        rel_path = '/rest/api/3/universal_avatar/view/type/{type}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_avatar_image_by_id(
        self,
        type: str,
        id: int,
        size: Optional[str] = None,
        format: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get avatar image by ID\n\nHTTP GET /rest/api/3/universal_avatar/view/type/{type}/avatar/{id}\nPath params:\n  - type (str)\n  - id (int)\nQuery params:\n  - size (str, optional)\n  - format (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'type': type,
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if size is not None:
            _query['size'] = size
        if format is not None:
            _query['format'] = format
        _body = None
        rel_path = '/rest/api/3/universal_avatar/view/type/{type}/avatar/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_avatar_image_by_owner(
        self,
        type: str,
        entityId: str,
        size: Optional[str] = None,
        format: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get avatar image by owner\n\nHTTP GET /rest/api/3/universal_avatar/view/type/{type}/owner/{entityId}\nPath params:\n  - type (str)\n  - entityId (str)\nQuery params:\n  - size (str, optional)\n  - format (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'type': type,
            'entityId': entityId,
        }
        _query: Dict[str, Any] = {}
        if size is not None:
            _query['size'] = size
        if format is not None:
            _query['format'] = format
        _body = None
        rel_path = '/rest/api/3/universal_avatar/view/type/{type}/owner/{entityId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def remove_user(
        self,
        accountId: str,
        username: Optional[str] = None,
        key: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete user\n\nHTTP DELETE /rest/api/3/user\nQuery params:\n  - accountId (str, required)\n  - username (str, optional)\n  - key (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['accountId'] = accountId
        if username is not None:
            _query['username'] = username
        if key is not None:
            _query['key'] = key
        _body = None
        rel_path = '/rest/api/3/user'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_user(
        self,
        accountId: Optional[str] = None,
        username: Optional[str] = None,
        key: Optional[str] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get user\n\nHTTP GET /rest/api/3/user\nQuery params:\n  - accountId (str, optional)\n  - username (str, optional)\n  - key (str, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if accountId is not None:
            _query['accountId'] = accountId
        if username is not None:
            _query['username'] = username
        if key is not None:
            _query['key'] = key
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/user'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_user(
        self,
        emailAddress: str,
        products: list[str],
        applicationKeys: Optional[list[str]] = None,
        displayName: Optional[str] = None,
        key: Optional[str] = None,
        name: Optional[str] = None,
        password: Optional[str] = None,
        self_: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create user\n\nHTTP POST /rest/api/3/user\nBody (application/json) fields:\n  - applicationKeys (list[str], optional)\n  - displayName (str, optional)\n  - emailAddress (str, required)\n  - key (str, optional)\n  - name (str, optional)\n  - password (str, optional)\n  - products (list[str], required)\n  - self (str, optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if applicationKeys is not None:
            _body['applicationKeys'] = applicationKeys
        if displayName is not None:
            _body['displayName'] = displayName
        _body['emailAddress'] = emailAddress
        if key is not None:
            _body['key'] = key
        if name is not None:
            _body['name'] = name
        if password is not None:
            _body['password'] = password
        _body['products'] = products
        if self_ is not None:
            _body['self'] = self_
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/user'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def find_bulk_assignable_users(
        self,
        projectKeys: str,
        query: Optional[str] = None,
        username: Optional[str] = None,
        accountId: Optional[str] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Find users assignable to projects\n\nHTTP GET /rest/api/3/user/assignable/multiProjectSearch\nQuery params:\n  - query (str, optional)\n  - username (str, optional)\n  - accountId (str, optional)\n  - projectKeys (str, required)\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if query is not None:
            _query['query'] = query
        if username is not None:
            _query['username'] = username
        if accountId is not None:
            _query['accountId'] = accountId
        _query['projectKeys'] = projectKeys
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/user/assignable/multiProjectSearch'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def find_assignable_users(
        self,
        query: Optional[str] = None,
        sessionId: Optional[str] = None,
        username: Optional[str] = None,
        accountId: Optional[str] = None,
        project: Optional[str] = None,
        issueKey: Optional[str] = None,
        issueId: Optional[str] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        actionDescriptorId: Optional[int] = None,
        recommend: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Find users assignable to issues\n\nHTTP GET /rest/api/3/user/assignable/search\nQuery params:\n  - query (str, optional)\n  - sessionId (str, optional)\n  - username (str, optional)\n  - accountId (str, optional)\n  - project (str, optional)\n  - issueKey (str, optional)\n  - issueId (str, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - actionDescriptorId (int, optional)\n  - recommend (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if query is not None:
            _query['query'] = query
        if sessionId is not None:
            _query['sessionId'] = sessionId
        if username is not None:
            _query['username'] = username
        if accountId is not None:
            _query['accountId'] = accountId
        if project is not None:
            _query['project'] = project
        if issueKey is not None:
            _query['issueKey'] = issueKey
        if issueId is not None:
            _query['issueId'] = issueId
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if actionDescriptorId is not None:
            _query['actionDescriptorId'] = actionDescriptorId
        if recommend is not None:
            _query['recommend'] = recommend
        _body = None
        rel_path = '/rest/api/3/user/assignable/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def bulk_get_users(
        self,
        accountId: list[str],
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        username: Optional[list[str]] = None,
        key: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk get users\n\nHTTP GET /rest/api/3/user/bulk\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - username (list[str], optional)\n  - key (list[str], optional)\n  - accountId (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if username is not None:
            _query['username'] = username
        if key is not None:
            _query['key'] = key
        _query['accountId'] = accountId
        _body = None
        rel_path = '/rest/api/3/user/bulk'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def bulk_get_users_migration(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        username: Optional[list[str]] = None,
        key: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get account IDs for users\n\nHTTP GET /rest/api/3/user/bulk/migration\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - username (list[str], optional)\n  - key (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if username is not None:
            _query['username'] = username
        if key is not None:
            _query['key'] = key
        _body = None
        rel_path = '/rest/api/3/user/bulk/migration'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def reset_user_columns(
        self,
        accountId: Optional[str] = None,
        username: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Reset user default columns\n\nHTTP DELETE /rest/api/3/user/columns\nQuery params:\n  - accountId (str, optional)\n  - username (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if accountId is not None:
            _query['accountId'] = accountId
        if username is not None:
            _query['username'] = username
        _body = None
        rel_path = '/rest/api/3/user/columns'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_user_default_columns(
        self,
        accountId: Optional[str] = None,
        username: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get user default columns\n\nHTTP GET /rest/api/3/user/columns\nQuery params:\n  - accountId (str, optional)\n  - username (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if accountId is not None:
            _query['accountId'] = accountId
        if username is not None:
            _query['username'] = username
        _body = None
        rel_path = '/rest/api/3/user/columns'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_user_columns(
        self,
        accountId: Optional[str] = None,
        columns: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set user default columns\n\nHTTP PUT /rest/api/3/user/columns\nQuery params:\n  - accountId (str, optional)\nBody (multipart/form-data) fields:\n  - columns (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'multipart/form-data')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if accountId is not None:
            _query['accountId'] = accountId
        _body: Dict[str, Any] = {}
        if columns is not None:
            _body['columns'] = columns
        rel_path = '/rest/api/3/user/columns'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_user_email(
        self,
        accountId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get user email\n\nHTTP GET /rest/api/3/user/email\nQuery params:\n  - accountId (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['accountId'] = accountId
        _body = None
        rel_path = '/rest/api/3/user/email'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_user_email_bulk(
        self,
        accountId: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get user email bulk\n\nHTTP GET /rest/api/3/user/email/bulk\nQuery params:\n  - accountId (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['accountId'] = accountId
        _body = None
        rel_path = '/rest/api/3/user/email/bulk'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_user_groups(
        self,
        accountId: str,
        username: Optional[str] = None,
        key: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get user groups\n\nHTTP GET /rest/api/3/user/groups\nQuery params:\n  - accountId (str, required)\n  - username (str, optional)\n  - key (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['accountId'] = accountId
        if username is not None:
            _query['username'] = username
        if key is not None:
            _query['key'] = key
        _body = None
        rel_path = '/rest/api/3/user/groups'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_user_nav_property(
        self,
        propertyKey: str,
        accountId: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get user nav property\n\nHTTP GET /rest/api/3/user/nav4-opt-property/{propertyKey}\nPath params:\n  - propertyKey (str)\nQuery params:\n  - accountId (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        if accountId is not None:
            _query['accountId'] = accountId
        _body = None
        rel_path = '/rest/api/3/user/nav4-opt-property/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_user_nav_property(
        self,
        propertyKey: str,
        accountId: Optional[str] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set user nav property\n\nHTTP PUT /rest/api/3/user/nav4-opt-property/{propertyKey}\nPath params:\n  - propertyKey (str)\nQuery params:\n  - accountId (str, optional)\nBody: application/json (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        if accountId is not None:
            _query['accountId'] = accountId
        _body = body
        rel_path = '/rest/api/3/user/nav4-opt-property/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def find_users_with_all_permissions(
        self,
        permissions: str,
        query: Optional[str] = None,
        username: Optional[str] = None,
        accountId: Optional[str] = None,
        issueKey: Optional[str] = None,
        projectKey: Optional[str] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Find users with permissions\n\nHTTP GET /rest/api/3/user/permission/search\nQuery params:\n  - query (str, optional)\n  - username (str, optional)\n  - accountId (str, optional)\n  - permissions (str, required)\n  - issueKey (str, optional)\n  - projectKey (str, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if query is not None:
            _query['query'] = query
        if username is not None:
            _query['username'] = username
        if accountId is not None:
            _query['accountId'] = accountId
        _query['permissions'] = permissions
        if issueKey is not None:
            _query['issueKey'] = issueKey
        if projectKey is not None:
            _query['projectKey'] = projectKey
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/user/permission/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def find_users_for_picker(
        self,
        query: str,
        maxResults: Optional[int] = None,
        showAvatar: Optional[bool] = None,
        exclude: Optional[list[str]] = None,
        excludeAccountIds: Optional[list[str]] = None,
        avatarSize: Optional[str] = None,
        excludeConnectUsers: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Find users for picker\n\nHTTP GET /rest/api/3/user/picker\nQuery params:\n  - query (str, required)\n  - maxResults (int, optional)\n  - showAvatar (bool, optional)\n  - exclude (list[str], optional)\n  - excludeAccountIds (list[str], optional)\n  - avatarSize (str, optional)\n  - excludeConnectUsers (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['query'] = query
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if showAvatar is not None:
            _query['showAvatar'] = showAvatar
        if exclude is not None:
            _query['exclude'] = exclude
        if excludeAccountIds is not None:
            _query['excludeAccountIds'] = excludeAccountIds
        if avatarSize is not None:
            _query['avatarSize'] = avatarSize
        if excludeConnectUsers is not None:
            _query['excludeConnectUsers'] = excludeConnectUsers
        _body = None
        rel_path = '/rest/api/3/user/picker'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_user_property_keys(
        self,
        accountId: Optional[str] = None,
        userKey: Optional[str] = None,
        username: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get user property keys\n\nHTTP GET /rest/api/3/user/properties\nQuery params:\n  - accountId (str, optional)\n  - userKey (str, optional)\n  - username (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if accountId is not None:
            _query['accountId'] = accountId
        if userKey is not None:
            _query['userKey'] = userKey
        if username is not None:
            _query['username'] = username
        _body = None
        rel_path = '/rest/api/3/user/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_user_property(
        self,
        propertyKey: str,
        accountId: Optional[str] = None,
        userKey: Optional[str] = None,
        username: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete user property\n\nHTTP DELETE /rest/api/3/user/properties/{propertyKey}\nPath params:\n  - propertyKey (str)\nQuery params:\n  - accountId (str, optional)\n  - userKey (str, optional)\n  - username (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        if accountId is not None:
            _query['accountId'] = accountId
        if userKey is not None:
            _query['userKey'] = userKey
        if username is not None:
            _query['username'] = username
        _body = None
        rel_path = '/rest/api/3/user/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_user_property(
        self,
        propertyKey: str,
        accountId: Optional[str] = None,
        userKey: Optional[str] = None,
        username: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get user property\n\nHTTP GET /rest/api/3/user/properties/{propertyKey}\nPath params:\n  - propertyKey (str)\nQuery params:\n  - accountId (str, optional)\n  - userKey (str, optional)\n  - username (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        if accountId is not None:
            _query['accountId'] = accountId
        if userKey is not None:
            _query['userKey'] = userKey
        if username is not None:
            _query['username'] = username
        _body = None
        rel_path = '/rest/api/3/user/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_user_property(
        self,
        propertyKey: str,
        accountId: Optional[str] = None,
        userKey: Optional[str] = None,
        username: Optional[str] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set user property\n\nHTTP PUT /rest/api/3/user/properties/{propertyKey}\nPath params:\n  - propertyKey (str)\nQuery params:\n  - accountId (str, optional)\n  - userKey (str, optional)\n  - username (str, optional)\nBody: application/json (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        if accountId is not None:
            _query['accountId'] = accountId
        if userKey is not None:
            _query['userKey'] = userKey
        if username is not None:
            _query['username'] = username
        _body = body
        rel_path = '/rest/api/3/user/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def find_users(
        self,
        query: Optional[str] = None,
        username: Optional[str] = None,
        accountId: Optional[str] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        property: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Find users\n\nHTTP GET /rest/api/3/user/search\nQuery params:\n  - query (str, optional)\n  - username (str, optional)\n  - accountId (str, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - property (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if query is not None:
            _query['query'] = query
        if username is not None:
            _query['username'] = username
        if accountId is not None:
            _query['accountId'] = accountId
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if property is not None:
            _query['property'] = property
        _body = None
        rel_path = '/rest/api/3/user/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def find_users_by_query(
        self,
        query: str,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Find users by query\n\nHTTP GET /rest/api/3/user/search/query\nQuery params:\n  - query (str, required)\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['query'] = query
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/user/search/query'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def find_user_keys_by_query(
        self,
        query: str,
        startAt: Optional[int] = None,
        maxResult: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Find user keys by query\n\nHTTP GET /rest/api/3/user/search/query/key\nQuery params:\n  - query (str, required)\n  - startAt (int, optional)\n  - maxResult (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['query'] = query
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResult is not None:
            _query['maxResult'] = maxResult
        _body = None
        rel_path = '/rest/api/3/user/search/query/key'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def find_users_with_browse_permission(
        self,
        query: Optional[str] = None,
        username: Optional[str] = None,
        accountId: Optional[str] = None,
        issueKey: Optional[str] = None,
        projectKey: Optional[str] = None,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Find users with browse permission\n\nHTTP GET /rest/api/3/user/viewissue/search\nQuery params:\n  - query (str, optional)\n  - username (str, optional)\n  - accountId (str, optional)\n  - issueKey (str, optional)\n  - projectKey (str, optional)\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if query is not None:
            _query['query'] = query
        if username is not None:
            _query['username'] = username
        if accountId is not None:
            _query['accountId'] = accountId
        if issueKey is not None:
            _query['issueKey'] = issueKey
        if projectKey is not None:
            _query['projectKey'] = projectKey
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/user/viewissue/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_users_default(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all users default\n\nHTTP GET /rest/api/3/users\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/users'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_users(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all users\n\nHTTP GET /rest/api/3/users/search\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/users/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_version(
        self,
        approvers: Optional[list[Dict[str, Any]]] = None,
        archived: Optional[bool] = None,
        description: Optional[str] = None,
        driver: Optional[str] = None,
        expand: Optional[str] = None,
        id: Optional[str] = None,
        issuesStatusForFixVersion: Optional[Dict[str, Any]] = None,
        moveUnfixedIssuesTo: Optional[str] = None,
        name: Optional[str] = None,
        operations: Optional[list[Dict[str, Any]]] = None,
        overdue: Optional[bool] = None,
        project: Optional[str] = None,
        projectId: Optional[int] = None,
        releaseDate: Optional[str] = None,
        released: Optional[bool] = None,
        self_: Optional[str] = None,
        startDate: Optional[str] = None,
        userReleaseDate: Optional[str] = None,
        userStartDate: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create version\n\nHTTP POST /rest/api/3/version\nBody (application/json) fields:\n  - approvers (list[Dict[str, Any]], optional)\n  - archived (bool, optional)\n  - description (str, optional)\n  - driver (str, optional)\n  - expand (str, optional)\n  - id (str, optional)\n  - issuesStatusForFixVersion (Dict[str, Any], optional)\n  - moveUnfixedIssuesTo (str, optional)\n  - name (str, optional)\n  - operations (list[Dict[str, Any]], optional)\n  - overdue (bool, optional)\n  - project (str, optional)\n  - projectId (int, optional)\n  - releaseDate (str, optional)\n  - released (bool, optional)\n  - self (str, optional)\n  - startDate (str, optional)\n  - userReleaseDate (str, optional)\n  - userStartDate (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if approvers is not None:
            _body['approvers'] = approvers
        if archived is not None:
            _body['archived'] = archived
        if description is not None:
            _body['description'] = description
        if driver is not None:
            _body['driver'] = driver
        if expand is not None:
            _body['expand'] = expand
        if id is not None:
            _body['id'] = id
        if issuesStatusForFixVersion is not None:
            _body['issuesStatusForFixVersion'] = issuesStatusForFixVersion
        if moveUnfixedIssuesTo is not None:
            _body['moveUnfixedIssuesTo'] = moveUnfixedIssuesTo
        if name is not None:
            _body['name'] = name
        if operations is not None:
            _body['operations'] = operations
        if overdue is not None:
            _body['overdue'] = overdue
        if project is not None:
            _body['project'] = project
        if projectId is not None:
            _body['projectId'] = projectId
        if releaseDate is not None:
            _body['releaseDate'] = releaseDate
        if released is not None:
            _body['released'] = released
        if self_ is not None:
            _body['self'] = self_
        if startDate is not None:
            _body['startDate'] = startDate
        if userReleaseDate is not None:
            _body['userReleaseDate'] = userReleaseDate
        if userStartDate is not None:
            _body['userStartDate'] = userStartDate
        rel_path = '/rest/api/3/version'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_version(
        self,
        id: str,
        moveFixIssuesTo: Optional[str] = None,
        moveAffectedIssuesTo: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete version\n\nHTTP DELETE /rest/api/3/version/{id}\nPath params:\n  - id (str)\nQuery params:\n  - moveFixIssuesTo (str, optional)\n  - moveAffectedIssuesTo (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if moveFixIssuesTo is not None:
            _query['moveFixIssuesTo'] = moveFixIssuesTo
        if moveAffectedIssuesTo is not None:
            _query['moveAffectedIssuesTo'] = moveAffectedIssuesTo
        _body = None
        rel_path = '/rest/api/3/version/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_version(
        self,
        id: str,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get version\n\nHTTP GET /rest/api/3/version/{id}\nPath params:\n  - id (str)\nQuery params:\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/version/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_version(
        self,
        id: str,
        approvers: Optional[list[Dict[str, Any]]] = None,
        archived: Optional[bool] = None,
        description: Optional[str] = None,
        driver: Optional[str] = None,
        expand: Optional[str] = None,
        id_body: Optional[str] = None,
        issuesStatusForFixVersion: Optional[Dict[str, Any]] = None,
        moveUnfixedIssuesTo: Optional[str] = None,
        name: Optional[str] = None,
        operations: Optional[list[Dict[str, Any]]] = None,
        overdue: Optional[bool] = None,
        project: Optional[str] = None,
        projectId: Optional[int] = None,
        releaseDate: Optional[str] = None,
        released: Optional[bool] = None,
        self_: Optional[str] = None,
        startDate: Optional[str] = None,
        userReleaseDate: Optional[str] = None,
        userStartDate: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update version\n\nHTTP PUT /rest/api/3/version/{id}\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - approvers (list[Dict[str, Any]], optional)\n  - archived (bool, optional)\n  - description (str, optional)\n  - driver (str, optional)\n  - expand (str, optional)\n  - id (str, optional)\n  - issuesStatusForFixVersion (Dict[str, Any], optional)\n  - moveUnfixedIssuesTo (str, optional)\n  - name (str, optional)\n  - operations (list[Dict[str, Any]], optional)\n  - overdue (bool, optional)\n  - project (str, optional)\n  - projectId (int, optional)\n  - releaseDate (str, optional)\n  - released (bool, optional)\n  - self (str, optional)\n  - startDate (str, optional)\n  - userReleaseDate (str, optional)\n  - userStartDate (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if approvers is not None:
            _body['approvers'] = approvers
        if archived is not None:
            _body['archived'] = archived
        if description is not None:
            _body['description'] = description
        if driver is not None:
            _body['driver'] = driver
        if expand is not None:
            _body['expand'] = expand
        if id_body is not None:
            _body['id'] = id_body
        if issuesStatusForFixVersion is not None:
            _body['issuesStatusForFixVersion'] = issuesStatusForFixVersion
        if moveUnfixedIssuesTo is not None:
            _body['moveUnfixedIssuesTo'] = moveUnfixedIssuesTo
        if name is not None:
            _body['name'] = name
        if operations is not None:
            _body['operations'] = operations
        if overdue is not None:
            _body['overdue'] = overdue
        if project is not None:
            _body['project'] = project
        if projectId is not None:
            _body['projectId'] = projectId
        if releaseDate is not None:
            _body['releaseDate'] = releaseDate
        if released is not None:
            _body['released'] = released
        if self_ is not None:
            _body['self'] = self_
        if startDate is not None:
            _body['startDate'] = startDate
        if userReleaseDate is not None:
            _body['userReleaseDate'] = userReleaseDate
        if userStartDate is not None:
            _body['userStartDate'] = userStartDate
        rel_path = '/rest/api/3/version/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def merge_versions(
        self,
        id: str,
        moveIssuesTo: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Merge versions\n\nHTTP PUT /rest/api/3/version/{id}/mergeto/{moveIssuesTo}\nPath params:\n  - id (str)\n  - moveIssuesTo (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
            'moveIssuesTo': moveIssuesTo,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/version/{id}/mergeto/{moveIssuesTo}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def move_version(
        self,
        id: str,
        after: Optional[str] = None,
        position: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Move version\n\nHTTP POST /rest/api/3/version/{id}/move\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - after (str, optional)\n  - position (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if after is not None:
            _body['after'] = after
        if position is not None:
            _body['position'] = position
        rel_path = '/rest/api/3/version/{id}/move'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_version_related_issues(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get version's related issues count\n\nHTTP GET /rest/api/3/version/{id}/relatedIssueCounts\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/version/{id}/relatedIssueCounts'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_related_work(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get related work\n\nHTTP GET /rest/api/3/version/{id}/relatedwork\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/version/{id}/relatedwork'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_related_work(
        self,
        id: str,
        category: str,
        issueId: Optional[int] = None,
        relatedWorkId: Optional[str] = None,
        title: Optional[str] = None,
        url: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create related work\n\nHTTP POST /rest/api/3/version/{id}/relatedwork\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - category (str, required)\n  - issueId (int, optional)\n  - relatedWorkId (str, optional)\n  - title (str, optional)\n  - url (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['category'] = category
        if issueId is not None:
            _body['issueId'] = issueId
        if relatedWorkId is not None:
            _body['relatedWorkId'] = relatedWorkId
        if title is not None:
            _body['title'] = title
        if url is not None:
            _body['url'] = url
        rel_path = '/rest/api/3/version/{id}/relatedwork'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_related_work(
        self,
        id: str,
        category: str,
        issueId: Optional[int] = None,
        relatedWorkId: Optional[str] = None,
        title: Optional[str] = None,
        url: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update related work\n\nHTTP PUT /rest/api/3/version/{id}/relatedwork\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - category (str, required)\n  - issueId (int, optional)\n  - relatedWorkId (str, optional)\n  - title (str, optional)\n  - url (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['category'] = category
        if issueId is not None:
            _body['issueId'] = issueId
        if relatedWorkId is not None:
            _body['relatedWorkId'] = relatedWorkId
        if title is not None:
            _body['title'] = title
        if url is not None:
            _body['url'] = url
        rel_path = '/rest/api/3/version/{id}/relatedwork'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_and_replace_version(
        self,
        id: str,
        customFieldReplacementList: Optional[list[Dict[str, Any]]] = None,
        moveAffectedIssuesTo: Optional[int] = None,
        moveFixIssuesTo: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete and replace version\n\nHTTP POST /rest/api/3/version/{id}/removeAndSwap\nPath params:\n  - id (str)\nBody (application/json) fields:\n  - customFieldReplacementList (list[Dict[str, Any]], optional)\n  - moveAffectedIssuesTo (int, optional)\n  - moveFixIssuesTo (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if customFieldReplacementList is not None:
            _body['customFieldReplacementList'] = customFieldReplacementList
        if moveAffectedIssuesTo is not None:
            _body['moveAffectedIssuesTo'] = moveAffectedIssuesTo
        if moveFixIssuesTo is not None:
            _body['moveFixIssuesTo'] = moveFixIssuesTo
        rel_path = '/rest/api/3/version/{id}/removeAndSwap'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_version_unresolved_issues(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get version's unresolved issues count\n\nHTTP GET /rest/api/3/version/{id}/unresolvedIssueCount\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/version/{id}/unresolvedIssueCount'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_related_work(
        self,
        versionId: str,
        relatedWorkId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete related work\n\nHTTP DELETE /rest/api/3/version/{versionId}/relatedwork/{relatedWorkId}\nPath params:\n  - versionId (str)\n  - relatedWorkId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'versionId': versionId,
            'relatedWorkId': relatedWorkId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/version/{versionId}/relatedwork/{relatedWorkId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_webhook_by_id(
        self,
        webhookIds: list[int],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete webhooks by ID\n\nHTTP DELETE /rest/api/3/webhook\nBody (application/json) fields:\n  - webhookIds (list[int], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['webhookIds'] = webhookIds
        rel_path = '/rest/api/3/webhook'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_dynamic_webhooks_for_app(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get dynamic webhooks for app\n\nHTTP GET /rest/api/3/webhook\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/webhook'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def register_dynamic_webhooks(
        self,
        url: str,
        webhooks: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Register dynamic webhooks\n\nHTTP POST /rest/api/3/webhook\nBody (application/json) fields:\n  - url (str, required)\n  - webhooks (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['url'] = url
        _body['webhooks'] = webhooks
        rel_path = '/rest/api/3/webhook'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_failed_webhooks(
        self,
        maxResults: Optional[int] = None,
        after: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get failed webhooks\n\nHTTP GET /rest/api/3/webhook/failed\nQuery params:\n  - maxResults (int, optional)\n  - after (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if after is not None:
            _query['after'] = after
        _body = None
        rel_path = '/rest/api/3/webhook/failed'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def refresh_webhooks(
        self,
        webhookIds: list[int],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Extend webhook life\n\nHTTP PUT /rest/api/3/webhook/refresh\nBody (application/json) fields:\n  - webhookIds (list[int], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['webhookIds'] = webhookIds
        rel_path = '/rest/api/3/webhook/refresh'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_workflows(
        self,
        workflowName: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all workflows\n\nHTTP GET /rest/api/3/workflow\nQuery params:\n  - workflowName (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if workflowName is not None:
            _query['workflowName'] = workflowName
        _body = None
        rel_path = '/rest/api/3/workflow'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_workflow(
        self,
        name: str,
        statuses: list[Dict[str, Any]],
        transitions: list[Dict[str, Any]],
        description: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create workflow\n\nHTTP POST /rest/api/3/workflow\nBody (application/json) fields:\n  - description (str, optional)\n  - name (str, required)\n  - statuses (list[Dict[str, Any]], required)\n  - transitions (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if description is not None:
            _body['description'] = description
        _body['name'] = name
        _body['statuses'] = statuses
        _body['transitions'] = transitions
        rel_path = '/rest/api/3/workflow'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workflow_transition_rule_configurations(
        self,
        types: list[str],
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        keys: Optional[list[str]] = None,
        workflowNames: Optional[list[str]] = None,
        withTags: Optional[list[str]] = None,
        draft: Optional[bool] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get workflow transition rule configurations\n\nHTTP GET /rest/api/3/workflow/rule/config\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - types (list[str], required)\n  - keys (list[str], optional)\n  - workflowNames (list[str], optional)\n  - withTags (list[str], optional)\n  - draft (bool, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _query['types'] = types
        if keys is not None:
            _query['keys'] = keys
        if workflowNames is not None:
            _query['workflowNames'] = workflowNames
        if withTags is not None:
            _query['withTags'] = withTags
        if draft is not None:
            _query['draft'] = draft
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/workflow/rule/config'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_workflow_transition_rule_configurations(
        self,
        workflows: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update workflow transition rule configurations\n\nHTTP PUT /rest/api/3/workflow/rule/config\nBody (application/json) fields:\n  - workflows (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['workflows'] = workflows
        rel_path = '/rest/api/3/workflow/rule/config'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_workflow_transition_rule_configurations(
        self,
        workflows: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete workflow transition rule configurations\n\nHTTP PUT /rest/api/3/workflow/rule/config/delete\nBody (application/json) fields:\n  - workflows (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['workflows'] = workflows
        rel_path = '/rest/api/3/workflow/rule/config/delete'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workflows_paginated(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        workflowName: Optional[list[str]] = None,
        expand: Optional[str] = None,
        queryString: Optional[str] = None,
        orderBy: Optional[str] = None,
        isActive: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get workflows paginated\n\nHTTP GET /rest/api/3/workflow/search\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - workflowName (list[str], optional)\n  - expand (str, optional)\n  - queryString (str, optional)\n  - orderBy (str, optional)\n  - isActive (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if workflowName is not None:
            _query['workflowName'] = workflowName
        if expand is not None:
            _query['expand'] = expand
        if queryString is not None:
            _query['queryString'] = queryString
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if isActive is not None:
            _query['isActive'] = isActive
        _body = None
        rel_path = '/rest/api/3/workflow/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_workflow_transition_property(
        self,
        transitionId: int,
        key: str,
        workflowName: str,
        workflowMode: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete workflow transition property\n\nHTTP DELETE /rest/api/3/workflow/transitions/{transitionId}/properties\nPath params:\n  - transitionId (int)\nQuery params:\n  - key (str, required)\n  - workflowName (str, required)\n  - workflowMode (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'transitionId': transitionId,
        }
        _query: Dict[str, Any] = {}
        _query['key'] = key
        _query['workflowName'] = workflowName
        if workflowMode is not None:
            _query['workflowMode'] = workflowMode
        _body = None
        rel_path = '/rest/api/3/workflow/transitions/{transitionId}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workflow_transition_properties(
        self,
        transitionId: int,
        workflowName: str,
        includeReservedKeys: Optional[bool] = None,
        key: Optional[str] = None,
        workflowMode: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get workflow transition properties\n\nHTTP GET /rest/api/3/workflow/transitions/{transitionId}/properties\nPath params:\n  - transitionId (int)\nQuery params:\n  - includeReservedKeys (bool, optional)\n  - key (str, optional)\n  - workflowName (str, required)\n  - workflowMode (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'transitionId': transitionId,
        }
        _query: Dict[str, Any] = {}
        if includeReservedKeys is not None:
            _query['includeReservedKeys'] = includeReservedKeys
        if key is not None:
            _query['key'] = key
        _query['workflowName'] = workflowName
        if workflowMode is not None:
            _query['workflowMode'] = workflowMode
        _body = None
        rel_path = '/rest/api/3/workflow/transitions/{transitionId}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_workflow_transition_property(
        self,
        transitionId: int,
        key: str,
        workflowName: str,
        value: str,
        workflowMode: Optional[str] = None,
        id: Optional[str] = None,
        key_body: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create workflow transition property\n\nHTTP POST /rest/api/3/workflow/transitions/{transitionId}/properties\nPath params:\n  - transitionId (int)\nQuery params:\n  - key (str, required)\n  - workflowName (str, required)\n  - workflowMode (str, optional)\nBody (application/json) fields:\n  - id (str, optional)\n  - key (str, optional)\n  - value (str, required)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'transitionId': transitionId,
        }
        _query: Dict[str, Any] = {}
        _query['key'] = key
        _query['workflowName'] = workflowName
        if workflowMode is not None:
            _query['workflowMode'] = workflowMode
        _body: Dict[str, Any] = {}
        if id is not None:
            _body['id'] = id
        if key_body is not None:
            _body['key'] = key_body
        _body['value'] = value
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/workflow/transitions/{transitionId}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_workflow_transition_property(
        self,
        transitionId: int,
        key: str,
        workflowName: str,
        value: str,
        workflowMode: Optional[str] = None,
        id: Optional[str] = None,
        key_body: Optional[str] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update workflow transition property\n\nHTTP PUT /rest/api/3/workflow/transitions/{transitionId}/properties\nPath params:\n  - transitionId (int)\nQuery params:\n  - key (str, required)\n  - workflowName (str, required)\n  - workflowMode (str, optional)\nBody (application/json) fields:\n  - id (str, optional)\n  - key (str, optional)\n  - value (str, required)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'transitionId': transitionId,
        }
        _query: Dict[str, Any] = {}
        _query['key'] = key
        _query['workflowName'] = workflowName
        if workflowMode is not None:
            _query['workflowMode'] = workflowMode
        _body: Dict[str, Any] = {}
        if id is not None:
            _body['id'] = id
        if key_body is not None:
            _body['key'] = key_body
        _body['value'] = value
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/workflow/transitions/{transitionId}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_inactive_workflow(
        self,
        entityId: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete inactive workflow\n\nHTTP DELETE /rest/api/3/workflow/{entityId}\nPath params:\n  - entityId (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'entityId': entityId,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/workflow/{entityId}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workflow_project_issue_type_usages(
        self,
        workflowId: str,
        projectId: int,
        nextPageToken: Optional[str] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue types in a project that are using a given workflow\n\nHTTP GET /rest/api/3/workflow/{workflowId}/project/{projectId}/issueTypeUsages\nPath params:\n  - workflowId (str)\n  - projectId (int)\nQuery params:\n  - nextPageToken (str, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'workflowId': workflowId,
            'projectId': projectId,
        }
        _query: Dict[str, Any] = {}
        if nextPageToken is not None:
            _query['nextPageToken'] = nextPageToken
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/workflow/{workflowId}/project/{projectId}/issueTypeUsages'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_usages_for_workflow(
        self,
        workflowId: str,
        nextPageToken: Optional[str] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get projects using a given workflow\n\nHTTP GET /rest/api/3/workflow/{workflowId}/projectUsages\nPath params:\n  - workflowId (str)\nQuery params:\n  - nextPageToken (str, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'workflowId': workflowId,
        }
        _query: Dict[str, Any] = {}
        if nextPageToken is not None:
            _query['nextPageToken'] = nextPageToken
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/workflow/{workflowId}/projectUsages'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workflow_scheme_usages_for_workflow(
        self,
        workflowId: str,
        nextPageToken: Optional[str] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get workflow schemes which are using a given workflow\n\nHTTP GET /rest/api/3/workflow/{workflowId}/workflowSchemes\nPath params:\n  - workflowId (str)\nQuery params:\n  - nextPageToken (str, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'workflowId': workflowId,
        }
        _query: Dict[str, Any] = {}
        if nextPageToken is not None:
            _query['nextPageToken'] = nextPageToken
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/workflow/{workflowId}/workflowSchemes'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def read_workflows(
        self,
        useApprovalConfiguration: Optional[bool] = None,
        projectAndIssueTypes: Optional[list[Dict[str, Any]]] = None,
        workflowIds: Optional[list[str]] = None,
        workflowNames: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk get workflows\n\nHTTP POST /rest/api/3/workflows\nQuery params:\n  - useApprovalConfiguration (bool, optional)\nBody (application/json) fields:\n  - projectAndIssueTypes (list[Dict[str, Any]], optional)\n  - workflowIds (list[str], optional)\n  - workflowNames (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if useApprovalConfiguration is not None:
            _query['useApprovalConfiguration'] = useApprovalConfiguration
        _body: Dict[str, Any] = {}
        if projectAndIssueTypes is not None:
            _body['projectAndIssueTypes'] = projectAndIssueTypes
        if workflowIds is not None:
            _body['workflowIds'] = workflowIds
        if workflowNames is not None:
            _body['workflowNames'] = workflowNames
        rel_path = '/rest/api/3/workflows'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def workflow_capabilities(
        self,
        workflowId: Optional[str] = None,
        projectId: Optional[str] = None,
        issueTypeId: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get available workflow capabilities\n\nHTTP GET /rest/api/3/workflows/capabilities\nQuery params:\n  - workflowId (str, optional)\n  - projectId (str, optional)\n  - issueTypeId (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if workflowId is not None:
            _query['workflowId'] = workflowId
        if projectId is not None:
            _query['projectId'] = projectId
        if issueTypeId is not None:
            _query['issueTypeId'] = issueTypeId
        _body = None
        rel_path = '/rest/api/3/workflows/capabilities'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_workflows(
        self,
        scope: Optional[Dict[str, Any]] = None,
        statuses: Optional[list[Dict[str, Any]]] = None,
        workflows: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk create workflows\n\nHTTP POST /rest/api/3/workflows/create\nBody (application/json) fields:\n  - scope (Dict[str, Any], optional)\n  - statuses (list[Dict[str, Any]], optional)\n  - workflows (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if scope is not None:
            _body['scope'] = scope
        if statuses is not None:
            _body['statuses'] = statuses
        if workflows is not None:
            _body['workflows'] = workflows
        rel_path = '/rest/api/3/workflows/create'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def validate_create_workflows(
        self,
        payload: Dict[str, Any],
        validationOptions: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Validate create workflows\n\nHTTP POST /rest/api/3/workflows/create/validation\nBody (application/json) fields:\n  - payload (Dict[str, Any], required)\n  - validationOptions (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['payload'] = payload
        if validationOptions is not None:
            _body['validationOptions'] = validationOptions
        rel_path = '/rest/api/3/workflows/create/validation'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_default_editor(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get the user's default workflow editor\n\nHTTP GET /rest/api/3/workflows/defaultEditor"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/workflows/defaultEditor'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def search_workflows(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        expand: Optional[str] = None,
        queryString: Optional[str] = None,
        orderBy: Optional[str] = None,
        scope: Optional[str] = None,
        isActive: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Search workflows\n\nHTTP GET /rest/api/3/workflows/search\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)\n  - expand (str, optional)\n  - queryString (str, optional)\n  - orderBy (str, optional)\n  - scope (str, optional)\n  - isActive (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        if expand is not None:
            _query['expand'] = expand
        if queryString is not None:
            _query['queryString'] = queryString
        if orderBy is not None:
            _query['orderBy'] = orderBy
        if scope is not None:
            _query['scope'] = scope
        if isActive is not None:
            _query['isActive'] = isActive
        _body = None
        rel_path = '/rest/api/3/workflows/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_workflows(
        self,
        statuses: Optional[list[Dict[str, Any]]] = None,
        workflows: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk update workflows\n\nHTTP POST /rest/api/3/workflows/update\nBody (application/json) fields:\n  - statuses (list[Dict[str, Any]], optional)\n  - workflows (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if statuses is not None:
            _body['statuses'] = statuses
        if workflows is not None:
            _body['workflows'] = workflows
        rel_path = '/rest/api/3/workflows/update'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def validate_update_workflows(
        self,
        payload: Dict[str, Any],
        validationOptions: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Validate update workflows\n\nHTTP POST /rest/api/3/workflows/update/validation\nBody (application/json) fields:\n  - payload (Dict[str, Any], required)\n  - validationOptions (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['payload'] = payload
        if validationOptions is not None:
            _body['validationOptions'] = validationOptions
        rel_path = '/rest/api/3/workflows/update/validation'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_all_workflow_schemes(
        self,
        startAt: Optional[int] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all workflow schemes\n\nHTTP GET /rest/api/3/workflowscheme\nQuery params:\n  - startAt (int, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if startAt is not None:
            _query['startAt'] = startAt
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/workflowscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_workflow_scheme(
        self,
        defaultWorkflow: Optional[str] = None,
        description: Optional[str] = None,
        draft: Optional[bool] = None,
        id: Optional[int] = None,
        issueTypeMappings: Optional[Dict[str, Any]] = None,
        issueTypes: Optional[Dict[str, Any]] = None,
        lastModified: Optional[str] = None,
        lastModifiedUser: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        originalDefaultWorkflow: Optional[str] = None,
        originalIssueTypeMappings: Optional[Dict[str, Any]] = None,
        self_: Optional[str] = None,
        updateDraftIfNeeded: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create workflow scheme\n\nHTTP POST /rest/api/3/workflowscheme\nBody (application/json) fields:\n  - defaultWorkflow (str, optional)\n  - description (str, optional)\n  - draft (bool, optional)\n  - id (int, optional)\n  - issueTypeMappings (Dict[str, Any], optional)\n  - issueTypes (Dict[str, Any], optional)\n  - lastModified (str, optional)\n  - lastModifiedUser (Dict[str, Any], optional)\n  - name (str, optional)\n  - originalDefaultWorkflow (str, optional)\n  - originalIssueTypeMappings (Dict[str, Any], optional)\n  - self (str, optional)\n  - updateDraftIfNeeded (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if defaultWorkflow is not None:
            _body['defaultWorkflow'] = defaultWorkflow
        if description is not None:
            _body['description'] = description
        if draft is not None:
            _body['draft'] = draft
        if id is not None:
            _body['id'] = id
        if issueTypeMappings is not None:
            _body['issueTypeMappings'] = issueTypeMappings
        if issueTypes is not None:
            _body['issueTypes'] = issueTypes
        if lastModified is not None:
            _body['lastModified'] = lastModified
        if lastModifiedUser is not None:
            _body['lastModifiedUser'] = lastModifiedUser
        if name is not None:
            _body['name'] = name
        if originalDefaultWorkflow is not None:
            _body['originalDefaultWorkflow'] = originalDefaultWorkflow
        if originalIssueTypeMappings is not None:
            _body['originalIssueTypeMappings'] = originalIssueTypeMappings
        if self_ is not None:
            _body['self'] = self_
        if updateDraftIfNeeded is not None:
            _body['updateDraftIfNeeded'] = updateDraftIfNeeded
        rel_path = '/rest/api/3/workflowscheme'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workflow_scheme_project_associations(
        self,
        projectId: list[int],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get workflow scheme project associations\n\nHTTP GET /rest/api/3/workflowscheme/project\nQuery params:\n  - projectId (list[int], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['projectId'] = projectId
        _body = None
        rel_path = '/rest/api/3/workflowscheme/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def assign_scheme_to_project(
        self,
        projectId: str,
        workflowSchemeId: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Assign workflow scheme to project\n\nHTTP PUT /rest/api/3/workflowscheme/project\nBody (application/json) fields:\n  - projectId (str, required)\n  - workflowSchemeId (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['projectId'] = projectId
        if workflowSchemeId is not None:
            _body['workflowSchemeId'] = workflowSchemeId
        rel_path = '/rest/api/3/workflowscheme/project'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def read_workflow_schemes(
        self,
        projectIds: Optional[list[str]] = None,
        workflowSchemeIds: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk get workflow schemes\n\nHTTP POST /rest/api/3/workflowscheme/read\nBody (application/json) fields:\n  - projectIds (list[str], optional)\n  - workflowSchemeIds (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if projectIds is not None:
            _body['projectIds'] = projectIds
        if workflowSchemeIds is not None:
            _body['workflowSchemeIds'] = workflowSchemeIds
        rel_path = '/rest/api/3/workflowscheme/read'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_schemes(
        self,
        description: str,
        id: str,
        name: str,
        version: Dict[str, Any],
        defaultWorkflowId: Optional[str] = None,
        statusMappingsByIssueTypeOverride: Optional[list[Dict[str, Any]]] = None,
        statusMappingsByWorkflows: Optional[list[Dict[str, Any]]] = None,
        workflowsForIssueTypes: Optional[list[Dict[str, Any]]] = None,
        body_additional: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update workflow scheme\n\nHTTP POST /rest/api/3/workflowscheme/update\nBody (application/json) fields:\n  - defaultWorkflowId (str, optional)\n  - description (str, required)\n  - id (str, required)\n  - name (str, required)\n  - statusMappingsByIssueTypeOverride (list[Dict[str, Any]], optional)\n  - statusMappingsByWorkflows (list[Dict[str, Any]], optional)\n  - version (Dict[str, Any], required)\n  - workflowsForIssueTypes (list[Dict[str, Any]], optional)\n  - additionalProperties allowed (pass via body_additional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if defaultWorkflowId is not None:
            _body['defaultWorkflowId'] = defaultWorkflowId
        _body['description'] = description
        _body['id'] = id
        _body['name'] = name
        if statusMappingsByIssueTypeOverride is not None:
            _body['statusMappingsByIssueTypeOverride'] = statusMappingsByIssueTypeOverride
        if statusMappingsByWorkflows is not None:
            _body['statusMappingsByWorkflows'] = statusMappingsByWorkflows
        _body['version'] = version
        if workflowsForIssueTypes is not None:
            _body['workflowsForIssueTypes'] = workflowsForIssueTypes
        if 'body_additional' in locals() and body_additional:
            _body.update(body_additional)
        rel_path = '/rest/api/3/workflowscheme/update'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_workflow_scheme_mappings(
        self,
        id: str,
        workflowsForIssueTypes: list[Dict[str, Any]],
        defaultWorkflowId: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get required status mappings for workflow scheme update\n\nHTTP POST /rest/api/3/workflowscheme/update/mappings\nBody (application/json) fields:\n  - defaultWorkflowId (str, optional)\n  - id (str, required)\n  - workflowsForIssueTypes (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if defaultWorkflowId is not None:
            _body['defaultWorkflowId'] = defaultWorkflowId
        _body['id'] = id
        _body['workflowsForIssueTypes'] = workflowsForIssueTypes
        rel_path = '/rest/api/3/workflowscheme/update/mappings'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_workflow_scheme(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete workflow scheme\n\nHTTP DELETE /rest/api/3/workflowscheme/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workflow_scheme(
        self,
        id: int,
        returnDraftIfExists: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get workflow scheme\n\nHTTP GET /rest/api/3/workflowscheme/{id}\nPath params:\n  - id (int)\nQuery params:\n  - returnDraftIfExists (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if returnDraftIfExists is not None:
            _query['returnDraftIfExists'] = returnDraftIfExists
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_workflow_scheme(
        self,
        id: int,
        defaultWorkflow: Optional[str] = None,
        description: Optional[str] = None,
        draft: Optional[bool] = None,
        id_body: Optional[int] = None,
        issueTypeMappings: Optional[Dict[str, Any]] = None,
        issueTypes: Optional[Dict[str, Any]] = None,
        lastModified: Optional[str] = None,
        lastModifiedUser: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        originalDefaultWorkflow: Optional[str] = None,
        originalIssueTypeMappings: Optional[Dict[str, Any]] = None,
        self_: Optional[str] = None,
        updateDraftIfNeeded: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Classic update workflow scheme\n\nHTTP PUT /rest/api/3/workflowscheme/{id}\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - defaultWorkflow (str, optional)\n  - description (str, optional)\n  - draft (bool, optional)\n  - id (int, optional)\n  - issueTypeMappings (Dict[str, Any], optional)\n  - issueTypes (Dict[str, Any], optional)\n  - lastModified (str, optional)\n  - lastModifiedUser (Dict[str, Any], optional)\n  - name (str, optional)\n  - originalDefaultWorkflow (str, optional)\n  - originalIssueTypeMappings (Dict[str, Any], optional)\n  - self (str, optional)\n  - updateDraftIfNeeded (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if defaultWorkflow is not None:
            _body['defaultWorkflow'] = defaultWorkflow
        if description is not None:
            _body['description'] = description
        if draft is not None:
            _body['draft'] = draft
        if id_body is not None:
            _body['id'] = id_body
        if issueTypeMappings is not None:
            _body['issueTypeMappings'] = issueTypeMappings
        if issueTypes is not None:
            _body['issueTypes'] = issueTypes
        if lastModified is not None:
            _body['lastModified'] = lastModified
        if lastModifiedUser is not None:
            _body['lastModifiedUser'] = lastModifiedUser
        if name is not None:
            _body['name'] = name
        if originalDefaultWorkflow is not None:
            _body['originalDefaultWorkflow'] = originalDefaultWorkflow
        if originalIssueTypeMappings is not None:
            _body['originalIssueTypeMappings'] = originalIssueTypeMappings
        if self_ is not None:
            _body['self'] = self_
        if updateDraftIfNeeded is not None:
            _body['updateDraftIfNeeded'] = updateDraftIfNeeded
        rel_path = '/rest/api/3/workflowscheme/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_workflow_scheme_draft_from_parent(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create draft workflow scheme\n\nHTTP POST /rest/api/3/workflowscheme/{id}/createdraft\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/createdraft'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_default_workflow(
        self,
        id: int,
        updateDraftIfNeeded: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete default workflow\n\nHTTP DELETE /rest/api/3/workflowscheme/{id}/default\nPath params:\n  - id (int)\nQuery params:\n  - updateDraftIfNeeded (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if updateDraftIfNeeded is not None:
            _query['updateDraftIfNeeded'] = updateDraftIfNeeded
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_default_workflow(
        self,
        id: int,
        returnDraftIfExists: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get default workflow\n\nHTTP GET /rest/api/3/workflowscheme/{id}/default\nPath params:\n  - id (int)\nQuery params:\n  - returnDraftIfExists (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if returnDraftIfExists is not None:
            _query['returnDraftIfExists'] = returnDraftIfExists
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_default_workflow(
        self,
        id: int,
        workflow: str,
        updateDraftIfNeeded: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update default workflow\n\nHTTP PUT /rest/api/3/workflowscheme/{id}/default\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - updateDraftIfNeeded (bool, optional)\n  - workflow (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if updateDraftIfNeeded is not None:
            _body['updateDraftIfNeeded'] = updateDraftIfNeeded
        _body['workflow'] = workflow
        rel_path = '/rest/api/3/workflowscheme/{id}/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_workflow_scheme_draft(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete draft workflow scheme\n\nHTTP DELETE /rest/api/3/workflowscheme/{id}/draft\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/draft'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workflow_scheme_draft(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get draft workflow scheme\n\nHTTP GET /rest/api/3/workflowscheme/{id}/draft\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/draft'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_workflow_scheme_draft(
        self,
        id: int,
        defaultWorkflow: Optional[str] = None,
        description: Optional[str] = None,
        draft: Optional[bool] = None,
        id_body: Optional[int] = None,
        issueTypeMappings: Optional[Dict[str, Any]] = None,
        issueTypes: Optional[Dict[str, Any]] = None,
        lastModified: Optional[str] = None,
        lastModifiedUser: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        originalDefaultWorkflow: Optional[str] = None,
        originalIssueTypeMappings: Optional[Dict[str, Any]] = None,
        self_: Optional[str] = None,
        updateDraftIfNeeded: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update draft workflow scheme\n\nHTTP PUT /rest/api/3/workflowscheme/{id}/draft\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - defaultWorkflow (str, optional)\n  - description (str, optional)\n  - draft (bool, optional)\n  - id (int, optional)\n  - issueTypeMappings (Dict[str, Any], optional)\n  - issueTypes (Dict[str, Any], optional)\n  - lastModified (str, optional)\n  - lastModifiedUser (Dict[str, Any], optional)\n  - name (str, optional)\n  - originalDefaultWorkflow (str, optional)\n  - originalIssueTypeMappings (Dict[str, Any], optional)\n  - self (str, optional)\n  - updateDraftIfNeeded (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if defaultWorkflow is not None:
            _body['defaultWorkflow'] = defaultWorkflow
        if description is not None:
            _body['description'] = description
        if draft is not None:
            _body['draft'] = draft
        if id_body is not None:
            _body['id'] = id_body
        if issueTypeMappings is not None:
            _body['issueTypeMappings'] = issueTypeMappings
        if issueTypes is not None:
            _body['issueTypes'] = issueTypes
        if lastModified is not None:
            _body['lastModified'] = lastModified
        if lastModifiedUser is not None:
            _body['lastModifiedUser'] = lastModifiedUser
        if name is not None:
            _body['name'] = name
        if originalDefaultWorkflow is not None:
            _body['originalDefaultWorkflow'] = originalDefaultWorkflow
        if originalIssueTypeMappings is not None:
            _body['originalIssueTypeMappings'] = originalIssueTypeMappings
        if self_ is not None:
            _body['self'] = self_
        if updateDraftIfNeeded is not None:
            _body['updateDraftIfNeeded'] = updateDraftIfNeeded
        rel_path = '/rest/api/3/workflowscheme/{id}/draft'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_draft_default_workflow(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete draft default workflow\n\nHTTP DELETE /rest/api/3/workflowscheme/{id}/draft/default\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/draft/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_draft_default_workflow(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get draft default workflow\n\nHTTP GET /rest/api/3/workflowscheme/{id}/draft/default\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/draft/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_draft_default_workflow(
        self,
        id: int,
        workflow: str,
        updateDraftIfNeeded: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update draft default workflow\n\nHTTP PUT /rest/api/3/workflowscheme/{id}/draft/default\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - updateDraftIfNeeded (bool, optional)\n  - workflow (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if updateDraftIfNeeded is not None:
            _body['updateDraftIfNeeded'] = updateDraftIfNeeded
        _body['workflow'] = workflow
        rel_path = '/rest/api/3/workflowscheme/{id}/draft/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_workflow_scheme_draft_issue_type(
        self,
        id: int,
        issueType: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete workflow for issue type in draft workflow scheme\n\nHTTP DELETE /rest/api/3/workflowscheme/{id}/draft/issuetype/{issueType}\nPath params:\n  - id (int)\n  - issueType (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
            'issueType': issueType,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/draft/issuetype/{issueType}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workflow_scheme_draft_issue_type(
        self,
        id: int,
        issueType: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get workflow for issue type in draft workflow scheme\n\nHTTP GET /rest/api/3/workflowscheme/{id}/draft/issuetype/{issueType}\nPath params:\n  - id (int)\n  - issueType (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
            'issueType': issueType,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/draft/issuetype/{issueType}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_workflow_scheme_draft_issue_type(
        self,
        id: int,
        issueType: str,
        issueType_body: Optional[str] = None,
        updateDraftIfNeeded: Optional[bool] = None,
        workflow: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set workflow for issue type in draft workflow scheme\n\nHTTP PUT /rest/api/3/workflowscheme/{id}/draft/issuetype/{issueType}\nPath params:\n  - id (int)\n  - issueType (str)\nBody (application/json) fields:\n  - issueType (str, optional)\n  - updateDraftIfNeeded (bool, optional)\n  - workflow (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
            'issueType': issueType,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if issueType_body is not None:
            _body['issueType'] = issueType_body
        if updateDraftIfNeeded is not None:
            _body['updateDraftIfNeeded'] = updateDraftIfNeeded
        if workflow is not None:
            _body['workflow'] = workflow
        rel_path = '/rest/api/3/workflowscheme/{id}/draft/issuetype/{issueType}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def publish_draft_workflow_scheme(
        self,
        id: int,
        validateOnly: Optional[bool] = None,
        statusMappings: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Publish draft workflow scheme\n\nHTTP POST /rest/api/3/workflowscheme/{id}/draft/publish\nPath params:\n  - id (int)\nQuery params:\n  - validateOnly (bool, optional)\nBody (application/json) fields:\n  - statusMappings (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if validateOnly is not None:
            _query['validateOnly'] = validateOnly
        _body: Dict[str, Any] = {}
        if statusMappings is not None:
            _body['statusMappings'] = statusMappings
        rel_path = '/rest/api/3/workflowscheme/{id}/draft/publish'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_draft_workflow_mapping(
        self,
        id: int,
        workflowName: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete issue types for workflow in draft workflow scheme\n\nHTTP DELETE /rest/api/3/workflowscheme/{id}/draft/workflow\nPath params:\n  - id (int)\nQuery params:\n  - workflowName (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _query['workflowName'] = workflowName
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/draft/workflow'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_draft_workflow(
        self,
        id: int,
        workflowName: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue types for workflows in draft workflow scheme\n\nHTTP GET /rest/api/3/workflowscheme/{id}/draft/workflow\nPath params:\n  - id (int)\nQuery params:\n  - workflowName (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if workflowName is not None:
            _query['workflowName'] = workflowName
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/draft/workflow'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_draft_workflow_mapping(
        self,
        id: int,
        workflowName: str,
        defaultMapping: Optional[bool] = None,
        issueTypes: Optional[list[str]] = None,
        updateDraftIfNeeded: Optional[bool] = None,
        workflow: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set issue types for workflow in workflow scheme\n\nHTTP PUT /rest/api/3/workflowscheme/{id}/draft/workflow\nPath params:\n  - id (int)\nQuery params:\n  - workflowName (str, required)\nBody (application/json) fields:\n  - defaultMapping (bool, optional)\n  - issueTypes (list[str], optional)\n  - updateDraftIfNeeded (bool, optional)\n  - workflow (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _query['workflowName'] = workflowName
        _body: Dict[str, Any] = {}
        if defaultMapping is not None:
            _body['defaultMapping'] = defaultMapping
        if issueTypes is not None:
            _body['issueTypes'] = issueTypes
        if updateDraftIfNeeded is not None:
            _body['updateDraftIfNeeded'] = updateDraftIfNeeded
        if workflow is not None:
            _body['workflow'] = workflow
        rel_path = '/rest/api/3/workflowscheme/{id}/draft/workflow'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_workflow_scheme_issue_type(
        self,
        id: int,
        issueType: str,
        updateDraftIfNeeded: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete workflow for issue type in workflow scheme\n\nHTTP DELETE /rest/api/3/workflowscheme/{id}/issuetype/{issueType}\nPath params:\n  - id (int)\n  - issueType (str)\nQuery params:\n  - updateDraftIfNeeded (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
            'issueType': issueType,
        }
        _query: Dict[str, Any] = {}
        if updateDraftIfNeeded is not None:
            _query['updateDraftIfNeeded'] = updateDraftIfNeeded
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/issuetype/{issueType}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workflow_scheme_issue_type(
        self,
        id: int,
        issueType: str,
        returnDraftIfExists: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get workflow for issue type in workflow scheme\n\nHTTP GET /rest/api/3/workflowscheme/{id}/issuetype/{issueType}\nPath params:\n  - id (int)\n  - issueType (str)\nQuery params:\n  - returnDraftIfExists (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
            'issueType': issueType,
        }
        _query: Dict[str, Any] = {}
        if returnDraftIfExists is not None:
            _query['returnDraftIfExists'] = returnDraftIfExists
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/issuetype/{issueType}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_workflow_scheme_issue_type(
        self,
        id: int,
        issueType: str,
        issueType_body: Optional[str] = None,
        updateDraftIfNeeded: Optional[bool] = None,
        workflow: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set workflow for issue type in workflow scheme\n\nHTTP PUT /rest/api/3/workflowscheme/{id}/issuetype/{issueType}\nPath params:\n  - id (int)\n  - issueType (str)\nBody (application/json) fields:\n  - issueType (str, optional)\n  - updateDraftIfNeeded (bool, optional)\n  - workflow (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
            'issueType': issueType,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if issueType_body is not None:
            _body['issueType'] = issueType_body
        if updateDraftIfNeeded is not None:
            _body['updateDraftIfNeeded'] = updateDraftIfNeeded
        if workflow is not None:
            _body['workflow'] = workflow
        rel_path = '/rest/api/3/workflowscheme/{id}/issuetype/{issueType}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_workflow_mapping(
        self,
        id: int,
        workflowName: str,
        updateDraftIfNeeded: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete issue types for workflow in workflow scheme\n\nHTTP DELETE /rest/api/3/workflowscheme/{id}/workflow\nPath params:\n  - id (int)\nQuery params:\n  - workflowName (str, required)\n  - updateDraftIfNeeded (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _query['workflowName'] = workflowName
        if updateDraftIfNeeded is not None:
            _query['updateDraftIfNeeded'] = updateDraftIfNeeded
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/workflow'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workflow(
        self,
        id: int,
        workflowName: Optional[str] = None,
        returnDraftIfExists: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get issue types for workflows in workflow scheme\n\nHTTP GET /rest/api/3/workflowscheme/{id}/workflow\nPath params:\n  - id (int)\nQuery params:\n  - workflowName (str, optional)\n  - returnDraftIfExists (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if workflowName is not None:
            _query['workflowName'] = workflowName
        if returnDraftIfExists is not None:
            _query['returnDraftIfExists'] = returnDraftIfExists
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{id}/workflow'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_workflow_mapping(
        self,
        id: int,
        workflowName: str,
        defaultMapping: Optional[bool] = None,
        issueTypes: Optional[list[str]] = None,
        updateDraftIfNeeded: Optional[bool] = None,
        workflow: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set issue types for workflow in workflow scheme\n\nHTTP PUT /rest/api/3/workflowscheme/{id}/workflow\nPath params:\n  - id (int)\nQuery params:\n  - workflowName (str, required)\nBody (application/json) fields:\n  - defaultMapping (bool, optional)\n  - issueTypes (list[str], optional)\n  - updateDraftIfNeeded (bool, optional)\n  - workflow (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _query['workflowName'] = workflowName
        _body: Dict[str, Any] = {}
        if defaultMapping is not None:
            _body['defaultMapping'] = defaultMapping
        if issueTypes is not None:
            _body['issueTypes'] = issueTypes
        if updateDraftIfNeeded is not None:
            _body['updateDraftIfNeeded'] = updateDraftIfNeeded
        if workflow is not None:
            _body['workflow'] = workflow
        rel_path = '/rest/api/3/workflowscheme/{id}/workflow'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_project_usages_for_workflow_scheme(
        self,
        workflowSchemeId: str,
        nextPageToken: Optional[str] = None,
        maxResults: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get projects which are using a given workflow scheme\n\nHTTP GET /rest/api/3/workflowscheme/{workflowSchemeId}/projectUsages\nPath params:\n  - workflowSchemeId (str)\nQuery params:\n  - nextPageToken (str, optional)\n  - maxResults (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'workflowSchemeId': workflowSchemeId,
        }
        _query: Dict[str, Any] = {}
        if nextPageToken is not None:
            _query['nextPageToken'] = nextPageToken
        if maxResults is not None:
            _query['maxResults'] = maxResults
        _body = None
        rel_path = '/rest/api/3/workflowscheme/{workflowSchemeId}/projectUsages'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_ids_of_worklogs_deleted_since(
        self,
        since: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get IDs of deleted worklogs\n\nHTTP GET /rest/api/3/worklog/deleted\nQuery params:\n  - since (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if since is not None:
            _query['since'] = since
        _body = None
        rel_path = '/rest/api/3/worklog/deleted'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_worklogs_for_ids(
        self,
        ids: list[int],
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get worklogs\n\nHTTP POST /rest/api/3/worklog/list\nQuery params:\n  - expand (str, optional)\nBody (application/json) fields:\n  - ids (list[int], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body: Dict[str, Any] = {}
        _body['ids'] = ids
        rel_path = '/rest/api/3/worklog/list'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_ids_of_worklogs_modified_since(
        self,
        since: Optional[int] = None,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get IDs of updated worklogs\n\nHTTP GET /rest/api/3/worklog/updated\nQuery params:\n  - since (int, optional)\n  - expand (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if since is not None:
            _query['since'] = since
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/rest/api/3/worklog/updated'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def addon_properties_resource_get_addon_properties_get(
        self,
        addonKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get app properties\n\nHTTP GET /rest/atlassian-connect/1/addons/{addonKey}/properties\nPath params:\n  - addonKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'addonKey': addonKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/atlassian-connect/1/addons/{addonKey}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def addon_properties_resource_delete_addon_property_delete(
        self,
        addonKey: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete app property\n\nHTTP DELETE /rest/atlassian-connect/1/addons/{addonKey}/properties/{propertyKey}\nPath params:\n  - addonKey (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'addonKey': addonKey,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/atlassian-connect/1/addons/{addonKey}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def addon_properties_resource_get_addon_property_get(
        self,
        addonKey: str,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get app property\n\nHTTP GET /rest/atlassian-connect/1/addons/{addonKey}/properties/{propertyKey}\nPath params:\n  - addonKey (str)\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'addonKey': addonKey,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/atlassian-connect/1/addons/{addonKey}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def addon_properties_resource_put_addon_property_put(
        self,
        addonKey: str,
        propertyKey: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set app property\n\nHTTP PUT /rest/atlassian-connect/1/addons/{addonKey}/properties/{propertyKey}\nPath params:\n  - addonKey (str)\n  - propertyKey (str)\nBody: application/json (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'addonKey': addonKey,
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/atlassian-connect/1/addons/{addonKey}/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def dynamic_modules_resource_remove_modules_delete(
        self,
        moduleKey: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove modules\n\nHTTP DELETE /rest/atlassian-connect/1/app/module/dynamic\nQuery params:\n  - moduleKey (list[str], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if moduleKey is not None:
            _query['moduleKey'] = moduleKey
        _body = None
        rel_path = '/rest/atlassian-connect/1/app/module/dynamic'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def dynamic_modules_resource_get_modules_get(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get modules\n\nHTTP GET /rest/atlassian-connect/1/app/module/dynamic"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/atlassian-connect/1/app/module/dynamic'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def dynamic_modules_resource_register_modules_post(
        self,
        modules: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Register modules\n\nHTTP POST /rest/atlassian-connect/1/app/module/dynamic\nBody (application/json) fields:\n  - modules (list[Dict[str, Any]], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['modules'] = modules
        rel_path = '/rest/atlassian-connect/1/app/module/dynamic'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def app_issue_field_value_update_resource_update_issue_fields_put(
        self,
        Atlassian_Transfer_Id: str,
        updateValueList: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk update custom field value\n\nHTTP PUT /rest/atlassian-connect/1/migration/field\nHeader params:\n  - Atlassian-Transfer-Id (str, required)\nBody (application/json) fields:\n  - updateValueList (list[Dict[str, Any]], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers['Atlassian-Transfer-Id'] = Atlassian_Transfer_Id
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if updateValueList is not None:
            _body['updateValueList'] = updateValueList
        rel_path = '/rest/atlassian-connect/1/migration/field'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def migration_resource_update_entity_properties_value_put(
        self,
        entityType: str,
        Atlassian_Transfer_Id: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Bulk update entity properties\n\nHTTP PUT /rest/atlassian-connect/1/migration/properties/{entityType}\nPath params:\n  - entityType (str)\nHeader params:\n  - Atlassian-Transfer-Id (str, required)\nBody: application/json (list[Dict[str, Any]])"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers['Atlassian-Transfer-Id'] = Atlassian_Transfer_Id
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'entityType': entityType,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/atlassian-connect/1/migration/properties/{entityType}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def migration_resource_workflow_rule_search_post(
        self,
        Atlassian_Transfer_Id: str,
        ruleIds: list[str],
        workflowEntityId: str,
        expand: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get workflow transition rule configurations\n\nHTTP POST /rest/atlassian-connect/1/migration/workflow/rule/search\nHeader params:\n  - Atlassian-Transfer-Id (str, required)\nBody (application/json) fields:\n  - expand (str, optional)\n  - ruleIds (list[str], required)\n  - workflowEntityId (str, required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers['Atlassian-Transfer-Id'] = Atlassian_Transfer_Id
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if expand is not None:
            _body['expand'] = expand
        _body['ruleIds'] = ruleIds
        _body['workflowEntityId'] = workflowEntityId
        rel_path = '/rest/atlassian-connect/1/migration/workflow/rule/search'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def service_registry_resource_services_get(
        self,
        serviceIds: list[str],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Retrieve the attributes of service registries\n\nHTTP GET /rest/atlassian-connect/1/service-registry\nQuery params:\n  - serviceIds (list[str], required)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['serviceIds'] = serviceIds
        _body = None
        rel_path = '/rest/atlassian-connect/1/service-registry'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_forge_app_property(
        self,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete app property (Forge)\n\nHTTP DELETE /rest/forge/1/app/properties/{propertyKey}\nPath params:\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/rest/forge/1/app/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def put_forge_app_property(
        self,
        propertyKey: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set app property (Forge)\n\nHTTP PUT /rest/forge/1/app/properties/{propertyKey}\nPath params:\n  - propertyKey (str)\nBody: application/json (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/rest/forge/1/app/properties/{propertyKey}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

# ---- Helpers used by generated methods ----
def _safe_format_url(template: str, params: Dict[str, object]) -> str:
    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return '{' + key + '}'
    try:
        return template.format_map(_SafeDict(params))
    except Exception:
        return template

def _to_bool_str(v: Union[bool, str, int, float]) -> str:
    if isinstance(v, bool):
        return 'true' if v else 'false'
    return str(v)

def _serialize_value(v: Union[bool, str, int, float, list, tuple, set, None]) -> str:
    if v is None:
        return ''
    if isinstance(v, (list, tuple, set)):
        return ','.join(_to_bool_str(x) for x in v)
    return _to_bool_str(v)

def _as_str_dict(d: Dict[str, Any]) -> Dict[str, str]:
    return {str(k): _serialize_value(v) for k, v in (d or {}).items()}
