from typing import Any, Dict, Optional, Union

from app.sources.client.confluence.confluence import ConfluenceClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse


class ConfluenceDataSource:
    def __init__(self, client: ConfluenceClient) -> None:
        """Default init for the connector-specific data source."""
        self._client = client.get_client()
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        try:
            self.base_url = self._client.get_base_url().rstrip('/') # type: ignore [valid method]
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'ConfluenceDataSource':
        return self

    async def get_admin_key(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Admin Key\n\nHTTP GET /admin-key"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/admin-key'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def enable_admin_key(
        self,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Enable Admin Key\n\nHTTP POST /admin-key\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/admin-key'
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

    async def disable_admin_key(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Disable Admin Key\n\nHTTP DELETE /admin-key"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/admin-key'
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

    async def get_attachments(
        self,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        status: Optional[list[str]] = None,
        mediaType: Optional[str] = None,
        filename: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get attachments\n\nHTTP GET /attachments\nQuery params:\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - status (list[str], optional)\n  - mediaType (str, optional)\n  - filename (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if status is not None:
            _query['status'] = status
        if mediaType is not None:
            _query['mediaType'] = mediaType
        if filename is not None:
            _query['filename'] = filename
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/attachments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_attachment_by_id(
        self,
        id: str,
        version: Optional[int] = None,
        include_labels: Optional[bool] = None,
        include_properties: Optional[bool] = None,
        include_operations: Optional[bool] = None,
        include_versions: Optional[bool] = None,
        include_version: Optional[bool] = None,
        include_collaborators: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get attachment by id\n\nHTTP GET /attachments/{id}\nPath params:\n  - id (str)\nQuery params:\n  - version (int, optional)\n  - include-labels (bool, optional)\n  - include-properties (bool, optional)\n  - include-operations (bool, optional)\n  - include-versions (bool, optional)\n  - include-version (bool, optional)\n  - include-collaborators (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if version is not None:
            _query['version'] = version
        if include_labels is not None:
            _query['include-labels'] = include_labels
        if include_properties is not None:
            _query['include-properties'] = include_properties
        if include_operations is not None:
            _query['include-operations'] = include_operations
        if include_versions is not None:
            _query['include-versions'] = include_versions
        if include_version is not None:
            _query['include-version'] = include_version
        if include_collaborators is not None:
            _query['include-collaborators'] = include_collaborators
        _body = None
        rel_path = '/attachments/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_attachment(
        self,
        id: int,
        purge: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete attachment\n\nHTTP DELETE /attachments/{id}\nPath params:\n  - id (int)\nQuery params:\n  - purge (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if purge is not None:
            _query['purge'] = purge
        _body = None
        rel_path = '/attachments/{id}'
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

    async def get_attachment_labels(
        self,
        id: int,
        prefix: Optional[str] = None,
        sort: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get labels for attachment\n\nHTTP GET /attachments/{id}/labels\nPath params:\n  - id (int)\nQuery params:\n  - prefix (str, optional)\n  - sort (str, optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if prefix is not None:
            _query['prefix'] = prefix
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/attachments/{id}/labels'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_attachment_operations(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permitted operations for attachment\n\nHTTP GET /attachments/{id}/operations\nPath params:\n  - id (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/attachments/{id}/operations'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_attachment_content_properties(
        self,
        attachment_id: str,
        key: Optional[str] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content properties for attachment\n\nHTTP GET /attachments/{attachment-id}/properties\nPath params:\n  - attachment-id (str)\nQuery params:\n  - key (str, optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'attachment-id': attachment_id,
        }
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/attachments/{attachment-id}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_attachment_property(
        self,
        attachment_id: str,
        key: Optional[str] = None,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create content property for attachment\n\nHTTP POST /attachments/{attachment-id}/properties\nPath params:\n  - attachment-id (str)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'attachment-id': attachment_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        rel_path = '/attachments/{attachment-id}/properties'
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

    async def get_attachment_content_properties_by_id(
        self,
        attachment_id: str,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content property for attachment by id\n\nHTTP GET /attachments/{attachment-id}/properties/{property-id}\nPath params:\n  - attachment-id (str)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'attachment-id': attachment_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/attachments/{attachment-id}/properties/{property-id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_attachment_property_by_id(
        self,
        attachment_id: str,
        property_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        version: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update content property for attachment by id\n\nHTTP PUT /attachments/{attachment-id}/properties/{property-id}\nPath params:\n  - attachment-id (str)\n  - property-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)\n  - version (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'attachment-id': attachment_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        if version is not None:
            _body['version'] = version
        rel_path = '/attachments/{attachment-id}/properties/{property-id}'
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

    async def delete_attachment_property_by_id(
        self,
        attachment_id: str,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete content property for attachment by id\n\nHTTP DELETE /attachments/{attachment-id}/properties/{property-id}\nPath params:\n  - attachment-id (str)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'attachment-id': attachment_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/attachments/{attachment-id}/properties/{property-id}'
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

    async def get_attachment_versions(
        self,
        id: str,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get attachment versions\n\nHTTP GET /attachments/{id}/versions\nPath params:\n  - id (str)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/attachments/{id}/versions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_attachment_version_details(
        self,
        attachment_id: str,
        version_number: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get version details for attachment version\n\nHTTP GET /attachments/{attachment-id}/versions/{version-number}\nPath params:\n  - attachment-id (str)\n  - version-number (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'attachment-id': attachment_id,
            'version-number': version_number,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/attachments/{attachment-id}/versions/{version-number}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_attachment_comments(
        self,
        id: str,
        body_format: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[Dict[str, Any]] = None,
        version: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get attachment comments\n\nHTTP GET /attachments/{id}/footer-comments\nPath params:\n  - id (str)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (Dict[str, Any], optional)\n  - version (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        if version is not None:
            _query['version'] = version
        _body = None
        rel_path = '/attachments/{id}/footer-comments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_blog_posts(
        self,
        id: Optional[list[int]] = None,
        space_id: Optional[list[int]] = None,
        sort: Optional[Dict[str, Any]] = None,
        status: Optional[list[str]] = None,
        title: Optional[str] = None,
        body_format: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get blog posts\n\nHTTP GET /blogposts\nQuery params:\n  - id (list[int], optional)\n  - space-id (list[int], optional)\n  - sort (Dict[str, Any], optional)\n  - status (list[str], optional)\n  - title (str, optional)\n  - body-format (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if id is not None:
            _query['id'] = id
        if space_id is not None:
            _query['space-id'] = space_id
        if sort is not None:
            _query['sort'] = sort
        if status is not None:
            _query['status'] = status
        if title is not None:
            _query['title'] = title
        if body_format is not None:
            _query['body-format'] = body_format
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/blogposts'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_blog_post(
        self,
        private: Optional[bool] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create blog post\n\nHTTP POST /blogposts\nQuery params:\n  - private (bool, optional)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if private is not None:
            _query['private'] = private
        _body = body
        rel_path = '/blogposts'
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

    async def get_blog_post_by_id(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        get_draft: Optional[bool] = None,
        status: Optional[list[str]] = None,
        version: Optional[int] = None,
        include_labels: Optional[bool] = None,
        include_properties: Optional[bool] = None,
        include_operations: Optional[bool] = None,
        include_likes: Optional[bool] = None,
        include_versions: Optional[bool] = None,
        include_version: Optional[bool] = None,
        include_favorited_by_current_user_status: Optional[bool] = None,
        include_webresources: Optional[bool] = None,
        include_collaborators: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get blog post by id\n\nHTTP GET /blogposts/{id}\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - get-draft (bool, optional)\n  - status (list[str], optional)\n  - version (int, optional)\n  - include-labels (bool, optional)\n  - include-properties (bool, optional)\n  - include-operations (bool, optional)\n  - include-likes (bool, optional)\n  - include-versions (bool, optional)\n  - include-version (bool, optional)\n  - include-favorited-by-current-user-status (bool, optional)\n  - include-webresources (bool, optional)\n  - include-collaborators (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if get_draft is not None:
            _query['get-draft'] = get_draft
        if status is not None:
            _query['status'] = status
        if version is not None:
            _query['version'] = version
        if include_labels is not None:
            _query['include-labels'] = include_labels
        if include_properties is not None:
            _query['include-properties'] = include_properties
        if include_operations is not None:
            _query['include-operations'] = include_operations
        if include_likes is not None:
            _query['include-likes'] = include_likes
        if include_versions is not None:
            _query['include-versions'] = include_versions
        if include_version is not None:
            _query['include-version'] = include_version
        if include_favorited_by_current_user_status is not None:
            _query['include-favorited-by-current-user-status'] = include_favorited_by_current_user_status
        if include_webresources is not None:
            _query['include-webresources'] = include_webresources
        if include_collaborators is not None:
            _query['include-collaborators'] = include_collaborators
        _body = None
        rel_path = '/blogposts/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_blog_post(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update blog post\n\nHTTP PUT /blogposts/{id}\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/blogposts/{id}'
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

    async def delete_blog_post(
        self,
        id: int,
        purge: Optional[bool] = None,
        draft: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete blog post\n\nHTTP DELETE /blogposts/{id}\nPath params:\n  - id (int)\nQuery params:\n  - purge (bool, optional)\n  - draft (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if purge is not None:
            _query['purge'] = purge
        if draft is not None:
            _query['draft'] = draft
        _body = None
        rel_path = '/blogposts/{id}'
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

    async def get_blogpost_attachments(
        self,
        id: int,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        status: Optional[list[str]] = None,
        mediaType: Optional[str] = None,
        filename: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get attachments for blog post\n\nHTTP GET /blogposts/{id}/attachments\nPath params:\n  - id (int)\nQuery params:\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - status (list[str], optional)\n  - mediaType (str, optional)\n  - filename (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if status is not None:
            _query['status'] = status
        if mediaType is not None:
            _query['mediaType'] = mediaType
        if filename is not None:
            _query['filename'] = filename
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/blogposts/{id}/attachments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_content_by_type_in_blog_post(
        self,
        id: int,
        type: str,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        body_format: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom content by type in blog post\n\nHTTP GET /blogposts/{id}/custom-content\nPath params:\n  - id (int)\nQuery params:\n  - type (str, required)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)\n  - body-format (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _query['type'] = type
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if body_format is not None:
            _query['body-format'] = body_format
        _body = None
        rel_path = '/blogposts/{id}/custom-content'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_blog_post_labels(
        self,
        id: int,
        prefix: Optional[str] = None,
        sort: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get labels for blog post\n\nHTTP GET /blogposts/{id}/labels\nPath params:\n  - id (int)\nQuery params:\n  - prefix (str, optional)\n  - sort (str, optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if prefix is not None:
            _query['prefix'] = prefix
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/blogposts/{id}/labels'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_blog_post_like_count(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get like count for blog post\n\nHTTP GET /blogposts/{id}/likes/count\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/blogposts/{id}/likes/count'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_blog_post_like_users(
        self,
        id: int,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get account IDs of likes for blog post\n\nHTTP GET /blogposts/{id}/likes/users\nPath params:\n  - id (int)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/blogposts/{id}/likes/users'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_blogpost_content_properties(
        self,
        blogpost_id: int,
        key: Optional[str] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content properties for blog post\n\nHTTP GET /blogposts/{blogpost-id}/properties\nPath params:\n  - blogpost-id (int)\nQuery params:\n  - key (str, optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'blogpost-id': blogpost_id,
        }
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/blogposts/{blogpost-id}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_blogpost_property(
        self,
        blogpost_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create content property for blog post\n\nHTTP POST /blogposts/{blogpost-id}/properties\nPath params:\n  - blogpost-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'blogpost-id': blogpost_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        rel_path = '/blogposts/{blogpost-id}/properties'
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

    async def get_blogpost_content_properties_by_id(
        self,
        blogpost_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content property for blog post by id\n\nHTTP GET /blogposts/{blogpost-id}/properties/{property-id}\nPath params:\n  - blogpost-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'blogpost-id': blogpost_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/blogposts/{blogpost-id}/properties/{property-id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_blogpost_property_by_id(
        self,
        blogpost_id: int,
        property_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        version: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update content property for blog post by id\n\nHTTP PUT /blogposts/{blogpost-id}/properties/{property-id}\nPath params:\n  - blogpost-id (int)\n  - property-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)\n  - version (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'blogpost-id': blogpost_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        if version is not None:
            _body['version'] = version
        rel_path = '/blogposts/{blogpost-id}/properties/{property-id}'
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

    async def delete_blogpost_property_by_id(
        self,
        blogpost_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete content property for blogpost by id\n\nHTTP DELETE /blogposts/{blogpost-id}/properties/{property-id}\nPath params:\n  - blogpost-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'blogpost-id': blogpost_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/blogposts/{blogpost-id}/properties/{property-id}'
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

    async def get_blog_post_operations(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permitted operations for blog post\n\nHTTP GET /blogposts/{id}/operations\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/blogposts/{id}/operations'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_blog_post_versions(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get blog post versions\n\nHTTP GET /blogposts/{id}/versions\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/blogposts/{id}/versions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_blog_post_version_details(
        self,
        blogpost_id: int,
        version_number: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get version details for blog post version\n\nHTTP GET /blogposts/{blogpost-id}/versions/{version-number}\nPath params:\n  - blogpost-id (int)\n  - version-number (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'blogpost-id': blogpost_id,
            'version-number': version_number,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/blogposts/{blogpost-id}/versions/{version-number}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def convert_content_ids_to_content_types(
        self,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Convert content ids to content types\n\nHTTP POST /content/convert-ids-to-types\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/content/convert-ids-to-types'
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

    async def get_custom_content_by_type(
        self,
        type: str,
        id: Optional[list[int]] = None,
        space_id: Optional[list[int]] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        body_format: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom content by type\n\nHTTP GET /custom-content\nQuery params:\n  - type (str, required)\n  - id (list[int], optional)\n  - space-id (list[int], optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)\n  - body-format (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _query['type'] = type
        if id is not None:
            _query['id'] = id
        if space_id is not None:
            _query['space-id'] = space_id
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if body_format is not None:
            _query['body-format'] = body_format
        _body = None
        rel_path = '/custom-content'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_custom_content(
        self,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create custom content\n\nHTTP POST /custom-content\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/custom-content'
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

    async def get_custom_content_by_id(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        version: Optional[int] = None,
        include_labels: Optional[bool] = None,
        include_properties: Optional[bool] = None,
        include_operations: Optional[bool] = None,
        include_versions: Optional[bool] = None,
        include_version: Optional[bool] = None,
        include_collaborators: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom content by id\n\nHTTP GET /custom-content/{id}\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - version (int, optional)\n  - include-labels (bool, optional)\n  - include-properties (bool, optional)\n  - include-operations (bool, optional)\n  - include-versions (bool, optional)\n  - include-version (bool, optional)\n  - include-collaborators (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if version is not None:
            _query['version'] = version
        if include_labels is not None:
            _query['include-labels'] = include_labels
        if include_properties is not None:
            _query['include-properties'] = include_properties
        if include_operations is not None:
            _query['include-operations'] = include_operations
        if include_versions is not None:
            _query['include-versions'] = include_versions
        if include_version is not None:
            _query['include-version'] = include_version
        if include_collaborators is not None:
            _query['include-collaborators'] = include_collaborators
        _body = None
        rel_path = '/custom-content/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_custom_content(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update custom content\n\nHTTP PUT /custom-content/{id}\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/custom-content/{id}'
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

    async def delete_custom_content(
        self,
        id: int,
        purge: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete custom content\n\nHTTP DELETE /custom-content/{id}\nPath params:\n  - id (int)\nQuery params:\n  - purge (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if purge is not None:
            _query['purge'] = purge
        _body = None
        rel_path = '/custom-content/{id}'
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

    async def get_custom_content_attachments(
        self,
        id: int,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        status: Optional[list[str]] = None,
        mediaType: Optional[str] = None,
        filename: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get attachments for custom content\n\nHTTP GET /custom-content/{id}/attachments\nPath params:\n  - id (int)\nQuery params:\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - status (list[str], optional)\n  - mediaType (str, optional)\n  - filename (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if status is not None:
            _query['status'] = status
        if mediaType is not None:
            _query['mediaType'] = mediaType
        if filename is not None:
            _query['filename'] = filename
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/custom-content/{id}/attachments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_content_comments(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom content comments\n\nHTTP GET /custom-content/{id}/footer-comments\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/custom-content/{id}/footer-comments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_content_labels(
        self,
        id: int,
        prefix: Optional[str] = None,
        sort: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get labels for custom content\n\nHTTP GET /custom-content/{id}/labels\nPath params:\n  - id (int)\nQuery params:\n  - prefix (str, optional)\n  - sort (str, optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if prefix is not None:
            _query['prefix'] = prefix
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/custom-content/{id}/labels'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_content_operations(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permitted operations for custom content\n\nHTTP GET /custom-content/{id}/operations\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/custom-content/{id}/operations'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_content_content_properties(
        self,
        custom_content_id: int,
        key: Optional[str] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content properties for custom content\n\nHTTP GET /custom-content/{custom-content-id}/properties\nPath params:\n  - custom-content-id (int)\nQuery params:\n  - key (str, optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'custom-content-id': custom_content_id,
        }
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/custom-content/{custom-content-id}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_custom_content_property(
        self,
        custom_content_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create content property for custom content\n\nHTTP POST /custom-content/{custom-content-id}/properties\nPath params:\n  - custom-content-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'custom-content-id': custom_content_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        rel_path = '/custom-content/{custom-content-id}/properties'
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

    async def get_custom_content_content_properties_by_id(
        self,
        custom_content_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content property for custom content by id\n\nHTTP GET /custom-content/{custom-content-id}/properties/{property-id}\nPath params:\n  - custom-content-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'custom-content-id': custom_content_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/custom-content/{custom-content-id}/properties/{property-id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_custom_content_property_by_id(
        self,
        custom_content_id: int,
        property_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        version: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update content property for custom content by id\n\nHTTP PUT /custom-content/{custom-content-id}/properties/{property-id}\nPath params:\n  - custom-content-id (int)\n  - property-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)\n  - version (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'custom-content-id': custom_content_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        if version is not None:
            _body['version'] = version
        rel_path = '/custom-content/{custom-content-id}/properties/{property-id}'
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

    async def delete_custom_content_property_by_id(
        self,
        custom_content_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete content property for custom content by id\n\nHTTP DELETE /custom-content/{custom-content-id}/properties/{property-id}\nPath params:\n  - custom-content-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'custom-content-id': custom_content_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/custom-content/{custom-content-id}/properties/{property-id}'
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

    async def get_labels(
        self,
        label_id: Optional[list[int]] = None,
        prefix: Optional[list[str]] = None,
        cursor: Optional[str] = None,
        sort: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get labels\n\nHTTP GET /labels\nQuery params:\n  - label-id (list[int], optional)\n  - prefix (list[str], optional)\n  - cursor (str, optional)\n  - sort (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if label_id is not None:
            _query['label-id'] = label_id
        if prefix is not None:
            _query['prefix'] = prefix
        if cursor is not None:
            _query['cursor'] = cursor
        if sort is not None:
            _query['sort'] = sort
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/labels'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_label_attachments(
        self,
        id: int,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get attachments for label\n\nHTTP GET /labels/{id}/attachments\nPath params:\n  - id (int)\nQuery params:\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/labels/{id}/attachments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_label_blog_posts(
        self,
        id: int,
        space_id: Optional[list[int]] = None,
        body_format: Optional[Dict[str, Any]] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get blog posts for label\n\nHTTP GET /labels/{id}/blogposts\nPath params:\n  - id (int)\nQuery params:\n  - space-id (list[int], optional)\n  - body-format (Dict[str, Any], optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if space_id is not None:
            _query['space-id'] = space_id
        if body_format is not None:
            _query['body-format'] = body_format
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/labels/{id}/blogposts'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_label_pages(
        self,
        id: int,
        space_id: Optional[list[int]] = None,
        body_format: Optional[Dict[str, Any]] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get pages for label\n\nHTTP GET /labels/{id}/pages\nPath params:\n  - id (int)\nQuery params:\n  - space-id (list[int], optional)\n  - body-format (Dict[str, Any], optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if space_id is not None:
            _query['space-id'] = space_id
        if body_format is not None:
            _query['body-format'] = body_format
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/labels/{id}/pages'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_pages(
        self,
        id: Optional[list[int]] = None,
        space_id: Optional[list[int]] = None,
        sort: Optional[Dict[str, Any]] = None,
        status: Optional[list[str]] = None,
        title: Optional[str] = None,
        body_format: Optional[Dict[str, Any]] = None,
        subtype: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get pages\n\nHTTP GET /pages\nQuery params:\n  - id (list[int], optional)\n  - space-id (list[int], optional)\n  - sort (Dict[str, Any], optional)\n  - status (list[str], optional)\n  - title (str, optional)\n  - body-format (Dict[str, Any], optional)\n  - subtype (str, optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if id is not None:
            _query['id'] = id
        if space_id is not None:
            _query['space-id'] = space_id
        if sort is not None:
            _query['sort'] = sort
        if status is not None:
            _query['status'] = status
        if title is not None:
            _query['title'] = title
        if body_format is not None:
            _query['body-format'] = body_format
        if subtype is not None:
            _query['subtype'] = subtype
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/pages'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_page(
        self,
        embedded: Optional[bool] = None,
        private: Optional[bool] = None,
        root_level: Optional[bool] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create page\n\nHTTP POST /pages\nQuery params:\n  - embedded (bool, optional)\n  - private (bool, optional)\n  - root-level (bool, optional)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if embedded is not None:
            _query['embedded'] = embedded
        if private is not None:
            _query['private'] = private
        if root_level is not None:
            _query['root-level'] = root_level
        _body = body
        rel_path = '/pages'
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

    async def get_page_by_id(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        get_draft: Optional[bool] = None,
        status: Optional[list[str]] = None,
        version: Optional[int] = None,
        include_labels: Optional[bool] = None,
        include_properties: Optional[bool] = None,
        include_operations: Optional[bool] = None,
        include_likes: Optional[bool] = None,
        include_versions: Optional[bool] = None,
        include_version: Optional[bool] = None,
        include_favorited_by_current_user_status: Optional[bool] = None,
        include_webresources: Optional[bool] = None,
        include_collaborators: Optional[bool] = None,
        include_direct_children: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get page by id\n\nHTTP GET /pages/{id}\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - get-draft (bool, optional)\n  - status (list[str], optional)\n  - version (int, optional)\n  - include-labels (bool, optional)\n  - include-properties (bool, optional)\n  - include-operations (bool, optional)\n  - include-likes (bool, optional)\n  - include-versions (bool, optional)\n  - include-version (bool, optional)\n  - include-favorited-by-current-user-status (bool, optional)\n  - include-webresources (bool, optional)\n  - include-collaborators (bool, optional)\n  - include-direct-children (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if get_draft is not None:
            _query['get-draft'] = get_draft
        if status is not None:
            _query['status'] = status
        if version is not None:
            _query['version'] = version
        if include_labels is not None:
            _query['include-labels'] = include_labels
        if include_properties is not None:
            _query['include-properties'] = include_properties
        if include_operations is not None:
            _query['include-operations'] = include_operations
        if include_likes is not None:
            _query['include-likes'] = include_likes
        if include_versions is not None:
            _query['include-versions'] = include_versions
        if include_version is not None:
            _query['include-version'] = include_version
        if include_favorited_by_current_user_status is not None:
            _query['include-favorited-by-current-user-status'] = include_favorited_by_current_user_status
        if include_webresources is not None:
            _query['include-webresources'] = include_webresources
        if include_collaborators is not None:
            _query['include-collaborators'] = include_collaborators
        if include_direct_children is not None:
            _query['include-direct-children'] = include_direct_children
        _body = None
        rel_path = '/pages/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_page(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update page\n\nHTTP PUT /pages/{id}\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/pages/{id}'
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

    async def delete_page(
        self,
        id: int,
        purge: Optional[bool] = None,
        draft: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete page\n\nHTTP DELETE /pages/{id}\nPath params:\n  - id (int)\nQuery params:\n  - purge (bool, optional)\n  - draft (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if purge is not None:
            _query['purge'] = purge
        if draft is not None:
            _query['draft'] = draft
        _body = None
        rel_path = '/pages/{id}'
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

    async def get_page_attachments(
        self,
        id: int,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        status: Optional[list[str]] = None,
        mediaType: Optional[str] = None,
        filename: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get attachments for page\n\nHTTP GET /pages/{id}/attachments\nPath params:\n  - id (int)\nQuery params:\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - status (list[str], optional)\n  - mediaType (str, optional)\n  - filename (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if status is not None:
            _query['status'] = status
        if mediaType is not None:
            _query['mediaType'] = mediaType
        if filename is not None:
            _query['filename'] = filename
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/pages/{id}/attachments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_content_by_type_in_page(
        self,
        id: int,
        type: str,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        body_format: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom content by type in page\n\nHTTP GET /pages/{id}/custom-content\nPath params:\n  - id (int)\nQuery params:\n  - type (str, required)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)\n  - body-format (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _query['type'] = type
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if body_format is not None:
            _query['body-format'] = body_format
        _body = None
        rel_path = '/pages/{id}/custom-content'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_page_labels(
        self,
        id: int,
        prefix: Optional[str] = None,
        sort: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get labels for page\n\nHTTP GET /pages/{id}/labels\nPath params:\n  - id (int)\nQuery params:\n  - prefix (str, optional)\n  - sort (str, optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if prefix is not None:
            _query['prefix'] = prefix
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/pages/{id}/labels'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_page_like_count(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get like count for page\n\nHTTP GET /pages/{id}/likes/count\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/pages/{id}/likes/count'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_page_like_users(
        self,
        id: int,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get account IDs of likes for page\n\nHTTP GET /pages/{id}/likes/users\nPath params:\n  - id (int)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/pages/{id}/likes/users'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_page_operations(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permitted operations for page\n\nHTTP GET /pages/{id}/operations\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/pages/{id}/operations'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_page_content_properties(
        self,
        page_id: int,
        key: Optional[str] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content properties for page\n\nHTTP GET /pages/{page-id}/properties\nPath params:\n  - page-id (int)\nQuery params:\n  - key (str, optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'page-id': page_id,
        }
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/pages/{page-id}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_page_property(
        self,
        page_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create content property for page\n\nHTTP POST /pages/{page-id}/properties\nPath params:\n  - page-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'page-id': page_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        rel_path = '/pages/{page-id}/properties'
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

    async def get_page_content_properties_by_id(
        self,
        page_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content property for page by id\n\nHTTP GET /pages/{page-id}/properties/{property-id}\nPath params:\n  - page-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'page-id': page_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/pages/{page-id}/properties/{property-id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_page_property_by_id(
        self,
        page_id: int,
        property_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        version: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update content property for page by id\n\nHTTP PUT /pages/{page-id}/properties/{property-id}\nPath params:\n  - page-id (int)\n  - property-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)\n  - version (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'page-id': page_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        if version is not None:
            _body['version'] = version
        rel_path = '/pages/{page-id}/properties/{property-id}'
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

    async def delete_page_property_by_id(
        self,
        page_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete content property for page by id\n\nHTTP DELETE /pages/{page-id}/properties/{property-id}\nPath params:\n  - page-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'page-id': page_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/pages/{page-id}/properties/{property-id}'
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

    async def post_redact_page(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Redact Content in a Confluence Page\n\nHTTP POST /pages/{id}/redact\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/pages/{id}/redact'
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

    async def post_redact_blog(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Redact Content in a Confluence Blog Post\n\nHTTP POST /blogposts/{id}/redact\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/blogposts/{id}/redact'
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

    async def update_page_title(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update page title\n\nHTTP PUT /pages/{id}/title\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/pages/{id}/title'
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

    async def get_page_versions(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get page versions\n\nHTTP GET /pages/{id}/versions\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/pages/{id}/versions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_whiteboard(
        self,
        private: Optional[bool] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create whiteboard\n\nHTTP POST /whiteboards\nQuery params:\n  - private (bool, optional)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if private is not None:
            _query['private'] = private
        _body = body
        rel_path = '/whiteboards'
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

    async def get_whiteboard_by_id(
        self,
        id: int,
        include_collaborators: Optional[bool] = None,
        include_direct_children: Optional[bool] = None,
        include_operations: Optional[bool] = None,
        include_properties: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get whiteboard by id\n\nHTTP GET /whiteboards/{id}\nPath params:\n  - id (int)\nQuery params:\n  - include-collaborators (bool, optional)\n  - include-direct-children (bool, optional)\n  - include-operations (bool, optional)\n  - include-properties (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if include_collaborators is not None:
            _query['include-collaborators'] = include_collaborators
        if include_direct_children is not None:
            _query['include-direct-children'] = include_direct_children
        if include_operations is not None:
            _query['include-operations'] = include_operations
        if include_properties is not None:
            _query['include-properties'] = include_properties
        _body = None
        rel_path = '/whiteboards/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_whiteboard(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete whiteboard\n\nHTTP DELETE /whiteboards/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/whiteboards/{id}'
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

    async def get_whiteboard_content_properties(
        self,
        id: int,
        key: Optional[str] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content properties for whiteboard\n\nHTTP GET /whiteboards/{id}/properties\nPath params:\n  - id (int)\nQuery params:\n  - key (str, optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/whiteboards/{id}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_whiteboard_property(
        self,
        id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create content property for whiteboard\n\nHTTP POST /whiteboards/{id}/properties\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        rel_path = '/whiteboards/{id}/properties'
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

    async def get_whiteboard_content_properties_by_id(
        self,
        whiteboard_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content property for whiteboard by id\n\nHTTP GET /whiteboards/{whiteboard-id}/properties/{property-id}\nPath params:\n  - whiteboard-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'whiteboard-id': whiteboard_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/whiteboards/{whiteboard-id}/properties/{property-id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_whiteboard_property_by_id(
        self,
        whiteboard_id: int,
        property_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        version: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update content property for whiteboard by id\n\nHTTP PUT /whiteboards/{whiteboard-id}/properties/{property-id}\nPath params:\n  - whiteboard-id (int)\n  - property-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)\n  - version (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'whiteboard-id': whiteboard_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        if version is not None:
            _body['version'] = version
        rel_path = '/whiteboards/{whiteboard-id}/properties/{property-id}'
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

    async def delete_whiteboard_property_by_id(
        self,
        whiteboard_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete content property for whiteboard by id\n\nHTTP DELETE /whiteboards/{whiteboard-id}/properties/{property-id}\nPath params:\n  - whiteboard-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'whiteboard-id': whiteboard_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/whiteboards/{whiteboard-id}/properties/{property-id}'
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

    async def get_whiteboard_operations(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permitted operations for a whiteboard\n\nHTTP GET /whiteboards/{id}/operations\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/whiteboards/{id}/operations'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_whiteboard_direct_children(
        self,
        id: int,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get direct children of a whiteboard\n\nHTTP GET /whiteboards/{id}/direct-children\nPath params:\n  - id (int)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/whiteboards/{id}/direct-children'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_whiteboard_descendants(
        self,
        id: int,
        limit: Optional[int] = None,
        depth: Optional[int] = None,
        cursor: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get descendants of a whiteboard\n\nHTTP GET /whiteboards/{id}/descendants\nPath params:\n  - id (int)\nQuery params:\n  - limit (int, optional)\n  - depth (int, optional)\n  - cursor (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if limit is not None:
            _query['limit'] = limit
        if depth is not None:
            _query['depth'] = depth
        if cursor is not None:
            _query['cursor'] = cursor
        _body = None
        rel_path = '/whiteboards/{id}/descendants'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_whiteboard_ancestors(
        self,
        id: int,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all ancestors of whiteboard\n\nHTTP GET /whiteboards/{id}/ancestors\nPath params:\n  - id (int)\nQuery params:\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/whiteboards/{id}/ancestors'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_database(
        self,
        private: Optional[bool] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create database\n\nHTTP POST /databases\nQuery params:\n  - private (bool, optional)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if private is not None:
            _query['private'] = private
        _body = body
        rel_path = '/databases'
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

    async def get_database_by_id(
        self,
        id: int,
        include_collaborators: Optional[bool] = None,
        include_direct_children: Optional[bool] = None,
        include_operations: Optional[bool] = None,
        include_properties: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get database by id\n\nHTTP GET /databases/{id}\nPath params:\n  - id (int)\nQuery params:\n  - include-collaborators (bool, optional)\n  - include-direct-children (bool, optional)\n  - include-operations (bool, optional)\n  - include-properties (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if include_collaborators is not None:
            _query['include-collaborators'] = include_collaborators
        if include_direct_children is not None:
            _query['include-direct-children'] = include_direct_children
        if include_operations is not None:
            _query['include-operations'] = include_operations
        if include_properties is not None:
            _query['include-properties'] = include_properties
        _body = None
        rel_path = '/databases/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_database(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete database\n\nHTTP DELETE /databases/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/databases/{id}'
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

    async def get_database_content_properties(
        self,
        id: int,
        key: Optional[str] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content properties for database\n\nHTTP GET /databases/{id}/properties\nPath params:\n  - id (int)\nQuery params:\n  - key (str, optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/databases/{id}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_database_property(
        self,
        id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create content property for database\n\nHTTP POST /databases/{id}/properties\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        rel_path = '/databases/{id}/properties'
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

    async def get_database_content_properties_by_id(
        self,
        database_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content property for database by id\n\nHTTP GET /databases/{database-id}/properties/{property-id}\nPath params:\n  - database-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'database-id': database_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/databases/{database-id}/properties/{property-id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_database_property_by_id(
        self,
        database_id: int,
        property_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        version: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update content property for database by id\n\nHTTP PUT /databases/{database-id}/properties/{property-id}\nPath params:\n  - database-id (int)\n  - property-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)\n  - version (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'database-id': database_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        if version is not None:
            _body['version'] = version
        rel_path = '/databases/{database-id}/properties/{property-id}'
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

    async def delete_database_property_by_id(
        self,
        database_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete content property for database by id\n\nHTTP DELETE /databases/{database-id}/properties/{property-id}\nPath params:\n  - database-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'database-id': database_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/databases/{database-id}/properties/{property-id}'
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

    async def get_database_operations(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permitted operations for a database\n\nHTTP GET /databases/{id}/operations\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/databases/{id}/operations'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_database_direct_children(
        self,
        id: int,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get direct children of a database\n\nHTTP GET /databases/{id}/direct-children\nPath params:\n  - id (int)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/databases/{id}/direct-children'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_database_descendants(
        self,
        id: int,
        limit: Optional[int] = None,
        depth: Optional[int] = None,
        cursor: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get descendants of a database\n\nHTTP GET /databases/{id}/descendants\nPath params:\n  - id (int)\nQuery params:\n  - limit (int, optional)\n  - depth (int, optional)\n  - cursor (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if limit is not None:
            _query['limit'] = limit
        if depth is not None:
            _query['depth'] = depth
        if cursor is not None:
            _query['cursor'] = cursor
        _body = None
        rel_path = '/databases/{id}/descendants'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_database_ancestors(
        self,
        id: int,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all ancestors of database\n\nHTTP GET /databases/{id}/ancestors\nPath params:\n  - id (int)\nQuery params:\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/databases/{id}/ancestors'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_smart_link(
        self,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create Smart Link in the content tree\n\nHTTP POST /embeds\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/embeds'
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

    async def get_smart_link_by_id(
        self,
        id: int,
        include_collaborators: Optional[bool] = None,
        include_direct_children: Optional[bool] = None,
        include_operations: Optional[bool] = None,
        include_properties: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Smart Link in the content tree by id\n\nHTTP GET /embeds/{id}\nPath params:\n  - id (int)\nQuery params:\n  - include-collaborators (bool, optional)\n  - include-direct-children (bool, optional)\n  - include-operations (bool, optional)\n  - include-properties (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if include_collaborators is not None:
            _query['include-collaborators'] = include_collaborators
        if include_direct_children is not None:
            _query['include-direct-children'] = include_direct_children
        if include_operations is not None:
            _query['include-operations'] = include_operations
        if include_properties is not None:
            _query['include-properties'] = include_properties
        _body = None
        rel_path = '/embeds/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_smart_link(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete Smart Link in the content tree\n\nHTTP DELETE /embeds/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/embeds/{id}'
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

    async def get_smart_link_content_properties(
        self,
        id: int,
        key: Optional[str] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content properties for Smart Link in the content tree\n\nHTTP GET /embeds/{id}/properties\nPath params:\n  - id (int)\nQuery params:\n  - key (str, optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/embeds/{id}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_smart_link_property(
        self,
        id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create content property for Smart Link in the content tree\n\nHTTP POST /embeds/{id}/properties\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        rel_path = '/embeds/{id}/properties'
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

    async def get_smart_link_content_properties_by_id(
        self,
        embed_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content property for Smart Link in the content tree by id\n\nHTTP GET /embeds/{embed-id}/properties/{property-id}\nPath params:\n  - embed-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'embed-id': embed_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/embeds/{embed-id}/properties/{property-id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_smart_link_property_by_id(
        self,
        embed_id: int,
        property_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        version: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update content property for Smart Link in the content tree by id\n\nHTTP PUT /embeds/{embed-id}/properties/{property-id}\nPath params:\n  - embed-id (int)\n  - property-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)\n  - version (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'embed-id': embed_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        if version is not None:
            _body['version'] = version
        rel_path = '/embeds/{embed-id}/properties/{property-id}'
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

    async def delete_smart_link_property_by_id(
        self,
        embed_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete content property for Smart Link in the content tree by id\n\nHTTP DELETE /embeds/{embed-id}/properties/{property-id}\nPath params:\n  - embed-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'embed-id': embed_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/embeds/{embed-id}/properties/{property-id}'
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

    async def get_smart_link_operations(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permitted operations for a Smart Link in the content tree\n\nHTTP GET /embeds/{id}/operations\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/embeds/{id}/operations'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_smart_link_direct_children(
        self,
        id: int,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get direct children of a Smart Link\n\nHTTP GET /embeds/{id}/direct-children\nPath params:\n  - id (int)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/embeds/{id}/direct-children'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_smart_link_descendants(
        self,
        id: int,
        limit: Optional[int] = None,
        depth: Optional[int] = None,
        cursor: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get descendants of a smart link\n\nHTTP GET /embeds/{id}/descendants\nPath params:\n  - id (int)\nQuery params:\n  - limit (int, optional)\n  - depth (int, optional)\n  - cursor (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if limit is not None:
            _query['limit'] = limit
        if depth is not None:
            _query['depth'] = depth
        if cursor is not None:
            _query['cursor'] = cursor
        _body = None
        rel_path = '/embeds/{id}/descendants'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_smart_link_ancestors(
        self,
        id: int,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all ancestors of Smart Link in content tree\n\nHTTP GET /embeds/{id}/ancestors\nPath params:\n  - id (int)\nQuery params:\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/embeds/{id}/ancestors'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_folder(
        self,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create folder\n\nHTTP POST /folders\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/folders'
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

    async def get_folder_by_id(
        self,
        id: int,
        include_collaborators: Optional[bool] = None,
        include_direct_children: Optional[bool] = None,
        include_operations: Optional[bool] = None,
        include_properties: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get folder by id\n\nHTTP GET /folders/{id}\nPath params:\n  - id (int)\nQuery params:\n  - include-collaborators (bool, optional)\n  - include-direct-children (bool, optional)\n  - include-operations (bool, optional)\n  - include-properties (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if include_collaborators is not None:
            _query['include-collaborators'] = include_collaborators
        if include_direct_children is not None:
            _query['include-direct-children'] = include_direct_children
        if include_operations is not None:
            _query['include-operations'] = include_operations
        if include_properties is not None:
            _query['include-properties'] = include_properties
        _body = None
        rel_path = '/folders/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_folder(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete folder\n\nHTTP DELETE /folders/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/folders/{id}'
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

    async def get_folder_content_properties(
        self,
        id: int,
        key: Optional[str] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content properties for folder\n\nHTTP GET /folders/{id}/properties\nPath params:\n  - id (int)\nQuery params:\n  - key (str, optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/folders/{id}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_folder_property(
        self,
        id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create content property for folder\n\nHTTP POST /folders/{id}/properties\nPath params:\n  - id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        rel_path = '/folders/{id}/properties'
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

    async def get_folder_content_properties_by_id(
        self,
        folder_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content property for folder by id\n\nHTTP GET /folders/{folder-id}/properties/{property-id}\nPath params:\n  - folder-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'folder-id': folder_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/folders/{folder-id}/properties/{property-id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_folder_property_by_id(
        self,
        folder_id: int,
        property_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        version: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update content property for folder by id\n\nHTTP PUT /folders/{folder-id}/properties/{property-id}\nPath params:\n  - folder-id (int)\n  - property-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)\n  - version (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'folder-id': folder_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        if version is not None:
            _body['version'] = version
        rel_path = '/folders/{folder-id}/properties/{property-id}'
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

    async def delete_folder_property_by_id(
        self,
        folder_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete content property for folder by id\n\nHTTP DELETE /folders/{folder-id}/properties/{property-id}\nPath params:\n  - folder-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'folder-id': folder_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/folders/{folder-id}/properties/{property-id}'
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

    async def get_folder_operations(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permitted operations for a folder\n\nHTTP GET /folders/{id}/operations\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/folders/{id}/operations'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_folder_direct_children(
        self,
        id: int,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get direct children of a folder\n\nHTTP GET /folders/{id}/direct-children\nPath params:\n  - id (int)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/folders/{id}/direct-children'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_folder_descendants(
        self,
        id: int,
        limit: Optional[int] = None,
        depth: Optional[int] = None,
        cursor: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get descendants of folder\n\nHTTP GET /folders/{id}/descendants\nPath params:\n  - id (int)\nQuery params:\n  - limit (int, optional)\n  - depth (int, optional)\n  - cursor (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if limit is not None:
            _query['limit'] = limit
        if depth is not None:
            _query['depth'] = depth
        if cursor is not None:
            _query['cursor'] = cursor
        _body = None
        rel_path = '/folders/{id}/descendants'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_folder_ancestors(
        self,
        id: int,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all ancestors of folder\n\nHTTP GET /folders/{id}/ancestors\nPath params:\n  - id (int)\nQuery params:\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/folders/{id}/ancestors'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_page_version_details(
        self,
        page_id: int,
        version_number: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get version details for page version\n\nHTTP GET /pages/{page-id}/versions/{version-number}\nPath params:\n  - page-id (int)\n  - version-number (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'page-id': page_id,
            'version-number': version_number,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/pages/{page-id}/versions/{version-number}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_content_versions(
        self,
        custom_content_id: int,
        body_format: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom content versions\n\nHTTP GET /custom-content/{custom-content-id}/versions\nPath params:\n  - custom-content-id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'custom-content-id': custom_content_id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/custom-content/{custom-content-id}/versions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_content_version_details(
        self,
        custom_content_id: int,
        version_number: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get version details for custom content version\n\nHTTP GET /custom-content/{custom-content-id}/versions/{version-number}\nPath params:\n  - custom-content-id (int)\n  - version-number (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'custom-content-id': custom_content_id,
            'version-number': version_number,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/custom-content/{custom-content-id}/versions/{version-number}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_spaces(
        self,
        ids: Optional[list[int]] = None,
        keys: Optional[list[str]] = None,
        type: Optional[str] = None,
        status: Optional[str] = None,
        labels: Optional[list[str]] = None,
        favorited_by: Optional[str] = None,
        not_favorited_by: Optional[str] = None,
        sort: Optional[Dict[str, Any]] = None,
        description_format: Optional[Dict[str, Any]] = None,
        include_icon: Optional[bool] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get spaces\n\nHTTP GET /spaces\nQuery params:\n  - ids (list[int], optional)\n  - keys (list[str], optional)\n  - type (str, optional)\n  - status (str, optional)\n  - labels (list[str], optional)\n  - favorited-by (str, optional)\n  - not-favorited-by (str, optional)\n  - sort (Dict[str, Any], optional)\n  - description-format (Dict[str, Any], optional)\n  - include-icon (bool, optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if ids is not None:
            _query['ids'] = ids
        if keys is not None:
            _query['keys'] = keys
        if type is not None:
            _query['type'] = type
        if status is not None:
            _query['status'] = status
        if labels is not None:
            _query['labels'] = labels
        if favorited_by is not None:
            _query['favorited-by'] = favorited_by
        if not_favorited_by is not None:
            _query['not-favorited-by'] = not_favorited_by
        if sort is not None:
            _query['sort'] = sort
        if description_format is not None:
            _query['description-format'] = description_format
        if include_icon is not None:
            _query['include-icon'] = include_icon
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/spaces'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_space(
        self,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create space\n\nHTTP POST /spaces\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/spaces'
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

    async def get_space_by_id(
        self,
        id: int,
        description_format: Optional[Dict[str, Any]] = None,
        include_icon: Optional[bool] = None,
        include_operations: Optional[bool] = None,
        include_properties: Optional[bool] = None,
        include_permissions: Optional[bool] = None,
        include_role_assignments: Optional[bool] = None,
        include_labels: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get space by id\n\nHTTP GET /spaces/{id}\nPath params:\n  - id (int)\nQuery params:\n  - description-format (Dict[str, Any], optional)\n  - include-icon (bool, optional)\n  - include-operations (bool, optional)\n  - include-properties (bool, optional)\n  - include-permissions (bool, optional)\n  - include-role-assignments (bool, optional)\n  - include-labels (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if description_format is not None:
            _query['description-format'] = description_format
        if include_icon is not None:
            _query['include-icon'] = include_icon
        if include_operations is not None:
            _query['include-operations'] = include_operations
        if include_properties is not None:
            _query['include-properties'] = include_properties
        if include_permissions is not None:
            _query['include-permissions'] = include_permissions
        if include_role_assignments is not None:
            _query['include-role-assignments'] = include_role_assignments
        if include_labels is not None:
            _query['include-labels'] = include_labels
        _body = None
        rel_path = '/spaces/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_blog_posts_in_space(
        self,
        id: int,
        sort: Optional[Dict[str, Any]] = None,
        status: Optional[list[str]] = None,
        title: Optional[str] = None,
        body_format: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get blog posts in space\n\nHTTP GET /spaces/{id}/blogposts\nPath params:\n  - id (int)\nQuery params:\n  - sort (Dict[str, Any], optional)\n  - status (list[str], optional)\n  - title (str, optional)\n  - body-format (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if sort is not None:
            _query['sort'] = sort
        if status is not None:
            _query['status'] = status
        if title is not None:
            _query['title'] = title
        if body_format is not None:
            _query['body-format'] = body_format
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/spaces/{id}/blogposts'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_space_labels(
        self,
        id: int,
        prefix: Optional[str] = None,
        sort: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get labels for space\n\nHTTP GET /spaces/{id}/labels\nPath params:\n  - id (int)\nQuery params:\n  - prefix (str, optional)\n  - sort (str, optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if prefix is not None:
            _query['prefix'] = prefix
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/spaces/{id}/labels'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_space_content_labels(
        self,
        id: int,
        prefix: Optional[str] = None,
        sort: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get labels for space content\n\nHTTP GET /spaces/{id}/content/labels\nPath params:\n  - id (int)\nQuery params:\n  - prefix (str, optional)\n  - sort (str, optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if prefix is not None:
            _query['prefix'] = prefix
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/spaces/{id}/content/labels'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_custom_content_by_type_in_space(
        self,
        id: int,
        type: str,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        body_format: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get custom content by type in space\n\nHTTP GET /spaces/{id}/custom-content\nPath params:\n  - id (int)\nQuery params:\n  - type (str, required)\n  - cursor (str, optional)\n  - limit (int, optional)\n  - body-format (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _query['type'] = type
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if body_format is not None:
            _query['body-format'] = body_format
        _body = None
        rel_path = '/spaces/{id}/custom-content'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_space_operations(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permitted operations for space\n\nHTTP GET /spaces/{id}/operations\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/spaces/{id}/operations'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_pages_in_space(
        self,
        id: str,
        depth: Optional[str] = None,
        sort: Optional[Dict[str, Any]] = None,
        status: Optional[list[str]] = None,
        title: Optional[str] = None,
        body_format: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get pages in space\n\nHTTP GET /spaces/{id}/pages\nPath params:\n  - id (int)\nQuery params:\n  - depth (str, optional)\n  - sort (Dict[str, Any], optional)\n  - status (list[str], optional)\n  - title (str, optional)\n  - body-format (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if depth is not None:
            _query['depth'] = depth
        if sort is not None:
            _query['sort'] = sort
        if status is not None:
            _query['status'] = status
        if title is not None:
            _query['title'] = title
        if body_format is not None:
            _query['body-format'] = body_format
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/spaces/{id}/pages'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_space_properties(
        self,
        space_id: int,
        key: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get space properties in space\n\nHTTP GET /spaces/{space-id}/properties\nPath params:\n  - space-id (int)\nQuery params:\n  - key (str, optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'space-id': space_id,
        }
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/spaces/{space-id}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_space_property(
        self,
        space_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create space property in space\n\nHTTP POST /spaces/{space-id}/properties\nPath params:\n  - space-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'space-id': space_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        rel_path = '/spaces/{space-id}/properties'
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

    async def get_space_property_by_id(
        self,
        space_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get space property by id\n\nHTTP GET /spaces/{space-id}/properties/{property-id}\nPath params:\n  - space-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'space-id': space_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/spaces/{space-id}/properties/{property-id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_space_property_by_id(
        self,
        space_id: int,
        property_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        version: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update space property by id\n\nHTTP PUT /spaces/{space-id}/properties/{property-id}\nPath params:\n  - space-id (int)\n  - property-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)\n  - version (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'space-id': space_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        if version is not None:
            _body['version'] = version
        rel_path = '/spaces/{space-id}/properties/{property-id}'
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

    async def delete_space_property_by_id(
        self,
        space_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete space property by id\n\nHTTP DELETE /spaces/{space-id}/properties/{property-id}\nPath params:\n  - space-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'space-id': space_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/spaces/{space-id}/properties/{property-id}'
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

    async def get_space_permissions_assignments(
        self,
        id: int,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get space permissions assignments\n\nHTTP GET /spaces/{id}/permissions\nPath params:\n  - id (int)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/spaces/{id}/permissions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_available_space_permissions(
        self,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get available space permissions\n\nHTTP GET /space-permissions\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/space-permissions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )

        resp = await self._client.execute(req)
        return resp

    async def get_available_space_roles(
        self,
        space_id: Optional[str] = None,
        role_type: Optional[str] = None,
        principal_id: Optional[str] = None,
        principal_type: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get available space roles\n\nHTTP GET /space-roles\nQuery params:\n  - space-id (str, optional)\n  - role-type (str, optional)\n  - principal-id (str, optional)\n  - principal-type (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if space_id is not None:
            _query['space-id'] = space_id
        if role_type is not None:
            _query['role-type'] = role_type
        if principal_id is not None:
            _query['principal-id'] = principal_id
        if principal_type is not None:
            _query['principal-type'] = principal_type
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/space-roles'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_space_roles_by_id(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get space role by ID\n\nHTTP GET /space-roles/{id}\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/space-roles/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_space_role_mode(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get space role mode\n\nHTTP GET /space-role-mode"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/space-role-mode'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_space_role_assignments(
        self,
        id: int,
        role_id: Optional[str] = None,
        role_type: Optional[str] = None,
        principal_id: Optional[str] = None,
        principal_type: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get space role assignments\n\nHTTP GET /spaces/{id}/role-assignments\nPath params:\n  - id (int)\nQuery params:\n  - role-id (str, optional)\n  - role-type (str, optional)\n  - principal-id (str, optional)\n  - principal-type (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if role_id is not None:
            _query['role-id'] = role_id
        if role_type is not None:
            _query['role-type'] = role_type
        if principal_id is not None:
            _query['principal-id'] = principal_id
        if principal_type is not None:
            _query['principal-type'] = principal_type
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/spaces/{id}/role-assignments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def set_space_role_assignments(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Set space role assignments\n\nHTTP POST /spaces/{id}/role-assignments\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/spaces/{id}/role-assignments'
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

    async def get_page_footer_comments(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        status: Optional[list[str]] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get footer comments for page\n\nHTTP GET /pages/{id}/footer-comments\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - status (list[str], optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if status is not None:
            _query['status'] = status
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/pages/{id}/footer-comments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_page_inline_comments(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        status: Optional[list[str]] = None,
        resolution_status: Optional[list[str]] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get inline comments for page\n\nHTTP GET /pages/{id}/inline-comments\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - status (list[str], optional)\n  - resolution-status (list[str], optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if status is not None:
            _query['status'] = status
        if resolution_status is not None:
            _query['resolution-status'] = resolution_status
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/pages/{id}/inline-comments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_blog_post_footer_comments(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        status: Optional[list[str]] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get footer comments for blog post\n\nHTTP GET /blogposts/{id}/footer-comments\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - status (list[str], optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if status is not None:
            _query['status'] = status
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/blogposts/{id}/footer-comments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_blog_post_inline_comments(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        status: Optional[list[str]] = None,
        resolution_status: Optional[list[str]] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get inline comments for blog post\n\nHTTP GET /blogposts/{id}/inline-comments\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - status (list[str], optional)\n  - resolution-status (list[str], optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if status is not None:
            _query['status'] = status
        if resolution_status is not None:
            _query['resolution-status'] = resolution_status
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/blogposts/{id}/inline-comments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_footer_comments(
        self,
        body_format: Optional[Dict[str, Any]] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get footer comments\n\nHTTP GET /footer-comments\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/footer-comments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_footer_comment(
        self,
        blogPostId: Optional[str] = None,
        pageId: Optional[str] = None,
        parentCommentId: Optional[str] = None,
        attachmentId: Optional[str] = None,
        customContentId: Optional[str] = None,
        body_body: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create footer comment\n\nHTTP POST /footer-comments\nBody (application/json) fields:\n  - blogPostId (str, optional)\n  - pageId (str, optional)\n  - parentCommentId (str, optional)\n  - attachmentId (str, optional)\n  - customContentId (str, optional)\n  - body (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if blogPostId is not None:
            _body['blogPostId'] = blogPostId
        if pageId is not None:
            _body['pageId'] = pageId
        if parentCommentId is not None:
            _body['parentCommentId'] = parentCommentId
        if attachmentId is not None:
            _body['attachmentId'] = attachmentId
        if customContentId is not None:
            _body['customContentId'] = customContentId
        if body_body is not None:
            _body['body'] = body_body
        rel_path = '/footer-comments'
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

    async def get_footer_comment_by_id(
        self,
        comment_id: int,
        body_format: Optional[Dict[str, Any]] = None,
        version: Optional[int] = None,
        include_properties: Optional[bool] = None,
        include_operations: Optional[bool] = None,
        include_likes: Optional[bool] = None,
        include_versions: Optional[bool] = None,
        include_version: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get footer comment by id\n\nHTTP GET /footer-comments/{comment-id}\nPath params:\n  - comment-id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - version (int, optional)\n  - include-properties (bool, optional)\n  - include-operations (bool, optional)\n  - include-likes (bool, optional)\n  - include-versions (bool, optional)\n  - include-version (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'comment-id': comment_id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if version is not None:
            _query['version'] = version
        if include_properties is not None:
            _query['include-properties'] = include_properties
        if include_operations is not None:
            _query['include-operations'] = include_operations
        if include_likes is not None:
            _query['include-likes'] = include_likes
        if include_versions is not None:
            _query['include-versions'] = include_versions
        if include_version is not None:
            _query['include-version'] = include_version
        _body = None
        rel_path = '/footer-comments/{comment-id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_footer_comment(
        self,
        comment_id: int,
        version: Optional[Dict[str, Any]] = None,
        body_body: Optional[str] = None,
        _links: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update footer comment\n\nHTTP PUT /footer-comments/{comment-id}\nPath params:\n  - comment-id (int)\nBody (application/json) fields:\n  - version (Dict[str, Any], optional)\n  - body (str, optional)\n  - _links (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'comment-id': comment_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if version is not None:
            _body['version'] = version
        if body_body is not None:
            _body['body'] = body_body
        if _links is not None:
            _body['_links'] = _links
        rel_path = '/footer-comments/{comment-id}'
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

    async def delete_footer_comment(
        self,
        comment_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete footer comment\n\nHTTP DELETE /footer-comments/{comment-id}\nPath params:\n  - comment-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'comment-id': comment_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/footer-comments/{comment-id}'
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

    async def get_footer_comment_children(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get children footer comments\n\nHTTP GET /footer-comments/{id}/children\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/footer-comments/{id}/children'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_footer_like_count(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get like count for footer comment\n\nHTTP GET /footer-comments/{id}/likes/count\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/footer-comments/{id}/likes/count'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_footer_like_users(
        self,
        id: int,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get account IDs of likes for footer comment\n\nHTTP GET /footer-comments/{id}/likes/users\nPath params:\n  - id (int)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/footer-comments/{id}/likes/users'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_footer_comment_operations(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permitted operations for footer comment\n\nHTTP GET /footer-comments/{id}/operations\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/footer-comments/{id}/operations'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_footer_comment_versions(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get footer comment versions\n\nHTTP GET /footer-comments/{id}/versions\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/footer-comments/{id}/versions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_footer_comment_version_details(
        self,
        id: int,
        version_number: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get version details for footer comment version\n\nHTTP GET /footer-comments/{id}/versions/{version-number}\nPath params:\n  - id (int)\n  - version-number (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
            'version-number': version_number,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/footer-comments/{id}/versions/{version-number}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_inline_comments(
        self,
        body_format: Optional[Dict[str, Any]] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get inline comments\n\nHTTP GET /inline-comments\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/inline-comments'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_inline_comment(
        self,
        blogPostId: Optional[str] = None,
        pageId: Optional[str] = None,
        parentCommentId: Optional[str] = None,
        body_body: Optional[str] = None,
        inlineCommentProperties: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create inline comment\n\nHTTP POST /inline-comments\nBody (application/json) fields:\n  - blogPostId (str, optional)\n  - pageId (str, optional)\n  - parentCommentId (str, optional)\n  - body (str, optional)\n  - inlineCommentProperties (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if blogPostId is not None:
            _body['blogPostId'] = blogPostId
        if pageId is not None:
            _body['pageId'] = pageId
        if parentCommentId is not None:
            _body['parentCommentId'] = parentCommentId
        if body_body is not None:
            _body['body'] = body_body
        if inlineCommentProperties is not None:
            _body['inlineCommentProperties'] = inlineCommentProperties
        rel_path = '/inline-comments'
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

    async def get_inline_comment_by_id(
        self,
        comment_id: int,
        body_format: Optional[Dict[str, Any]] = None,
        version: Optional[int] = None,
        include_properties: Optional[bool] = None,
        include_operations: Optional[bool] = None,
        include_likes: Optional[bool] = None,
        include_versions: Optional[bool] = None,
        include_version: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get inline comment by id\n\nHTTP GET /inline-comments/{comment-id}\nPath params:\n  - comment-id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - version (int, optional)\n  - include-properties (bool, optional)\n  - include-operations (bool, optional)\n  - include-likes (bool, optional)\n  - include-versions (bool, optional)\n  - include-version (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'comment-id': comment_id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if version is not None:
            _query['version'] = version
        if include_properties is not None:
            _query['include-properties'] = include_properties
        if include_operations is not None:
            _query['include-operations'] = include_operations
        if include_likes is not None:
            _query['include-likes'] = include_likes
        if include_versions is not None:
            _query['include-versions'] = include_versions
        if include_version is not None:
            _query['include-version'] = include_version
        _body = None
        rel_path = '/inline-comments/{comment-id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_inline_comment(
        self,
        comment_id: int,
        version: Optional[Dict[str, Any]] = None,
        body_body: Optional[str] = None,
        resolved: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update inline comment\n\nHTTP PUT /inline-comments/{comment-id}\nPath params:\n  - comment-id (int)\nBody (application/json) fields:\n  - version (Dict[str, Any], optional)\n  - body (str, optional)\n  - resolved (bool, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'comment-id': comment_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if version is not None:
            _body['version'] = version
        if body_body is not None:
            _body['body'] = body_body
        if resolved is not None:
            _body['resolved'] = resolved
        rel_path = '/inline-comments/{comment-id}'
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

    async def delete_inline_comment(
        self,
        comment_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete inline comment\n\nHTTP DELETE /inline-comments/{comment-id}\nPath params:\n  - comment-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'comment-id': comment_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/inline-comments/{comment-id}'
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

    async def get_inline_comment_children(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get children inline comments\n\nHTTP GET /inline-comments/{id}/children\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/inline-comments/{id}/children'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_inline_like_count(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get like count for inline comment\n\nHTTP GET /inline-comments/{id}/likes/count\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/inline-comments/{id}/likes/count'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_inline_like_users(
        self,
        id: int,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get account IDs of likes for inline comment\n\nHTTP GET /inline-comments/{id}/likes/users\nPath params:\n  - id (int)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/inline-comments/{id}/likes/users'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_inline_comment_operations(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get permitted operations for inline comment\n\nHTTP GET /inline-comments/{id}/operations\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/inline-comments/{id}/operations'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_inline_comment_versions(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get inline comment versions\n\nHTTP GET /inline-comments/{id}/versions\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/inline-comments/{id}/versions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_inline_comment_version_details(
        self,
        id: int,
        version_number: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get version details for inline comment version\n\nHTTP GET /inline-comments/{id}/versions/{version-number}\nPath params:\n  - id (int)\n  - version-number (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
            'version-number': version_number,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/inline-comments/{id}/versions/{version-number}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_comment_content_properties(
        self,
        comment_id: int,
        key: Optional[str] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content properties for comment\n\nHTTP GET /comments/{comment-id}/properties\nPath params:\n  - comment-id (int)\nQuery params:\n  - key (str, optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'comment-id': comment_id,
        }
        _query: Dict[str, Any] = {}
        if key is not None:
            _query['key'] = key
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/comments/{comment-id}/properties'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_comment_property(
        self,
        comment_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create content property for comment\n\nHTTP POST /comments/{comment-id}/properties\nPath params:\n  - comment-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'comment-id': comment_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        rel_path = '/comments/{comment-id}/properties'
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

    async def get_comment_content_properties_by_id(
        self,
        comment_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get content property for comment by id\n\nHTTP GET /comments/{comment-id}/properties/{property-id}\nPath params:\n  - comment-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'comment-id': comment_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/comments/{comment-id}/properties/{property-id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_comment_property_by_id(
        self,
        comment_id: int,
        property_id: int,
        key: Optional[str] = None,
        value: Optional[str] = None,
        version: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update content property for comment by id\n\nHTTP PUT /comments/{comment-id}/properties/{property-id}\nPath params:\n  - comment-id (int)\n  - property-id (int)\nBody (application/json) fields:\n  - key (str, optional)\n  - value (str, optional)\n  - version (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'comment-id': comment_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if key is not None:
            _body['key'] = key
        if value is not None:
            _body['value'] = value
        if version is not None:
            _body['version'] = version
        rel_path = '/comments/{comment-id}/properties/{property-id}'
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

    async def delete_comment_property_by_id(
        self,
        comment_id: int,
        property_id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete content property for comment by id\n\nHTTP DELETE /comments/{comment-id}/properties/{property-id}\nPath params:\n  - comment-id (int)\n  - property-id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'comment-id': comment_id,
            'property-id': property_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/comments/{comment-id}/properties/{property-id}'
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

    async def get_tasks(
        self,
        body_format: Optional[Dict[str, Any]] = None,
        include_blank_tasks: Optional[bool] = None,
        status: Optional[str] = None,
        task_id: Optional[list[int]] = None,
        space_id: Optional[list[int]] = None,
        page_id: Optional[list[int]] = None,
        blogpost_id: Optional[list[int]] = None,
        created_by: Optional[list[str]] = None,
        assigned_to: Optional[list[str]] = None,
        completed_by: Optional[list[str]] = None,
        created_at_from: Optional[int] = None,
        created_at_to: Optional[int] = None,
        due_at_from: Optional[int] = None,
        due_at_to: Optional[int] = None,
        completed_at_from: Optional[int] = None,
        completed_at_to: Optional[int] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get tasks\n\nHTTP GET /tasks\nQuery params:\n  - body-format (Dict[str, Any], optional)\n  - include-blank-tasks (bool, optional)\n  - status (str, optional)\n  - task-id (list[int], optional)\n  - space-id (list[int], optional)\n  - page-id (list[int], optional)\n  - blogpost-id (list[int], optional)\n  - created-by (list[str], optional)\n  - assigned-to (list[str], optional)\n  - completed-by (list[str], optional)\n  - created-at-from (int, optional)\n  - created-at-to (int, optional)\n  - due-at-from (int, optional)\n  - due-at-to (int, optional)\n  - completed-at-from (int, optional)\n  - completed-at-to (int, optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        if include_blank_tasks is not None:
            _query['include-blank-tasks'] = include_blank_tasks
        if status is not None:
            _query['status'] = status
        if task_id is not None:
            _query['task-id'] = task_id
        if space_id is not None:
            _query['space-id'] = space_id
        if page_id is not None:
            _query['page-id'] = page_id
        if blogpost_id is not None:
            _query['blogpost-id'] = blogpost_id
        if created_by is not None:
            _query['created-by'] = created_by
        if assigned_to is not None:
            _query['assigned-to'] = assigned_to
        if completed_by is not None:
            _query['completed-by'] = completed_by
        if created_at_from is not None:
            _query['created-at-from'] = created_at_from
        if created_at_to is not None:
            _query['created-at-to'] = created_at_to
        if due_at_from is not None:
            _query['due-at-from'] = due_at_from
        if due_at_to is not None:
            _query['due-at-to'] = due_at_to
        if completed_at_from is not None:
            _query['completed-at-from'] = completed_at_from
        if completed_at_to is not None:
            _query['completed-at-to'] = completed_at_to
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/tasks'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_task_by_id(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get task by id\n\nHTTP GET /tasks/{id}\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        _body = None
        rel_path = '/tasks/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_task(
        self,
        id: int,
        body_format: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update task\n\nHTTP PUT /tasks/{id}\nPath params:\n  - id (int)\nQuery params:\n  - body-format (Dict[str, Any], optional)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if body_format is not None:
            _query['body-format'] = body_format
        _body = body
        rel_path = '/tasks/{id}'
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

    async def get_child_pages(
        self,
        id: int,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get child pages\n\nHTTP GET /pages/{id}/children\nPath params:\n  - id (int)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/pages/{id}/children'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_child_custom_content(
        self,
        id: int,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get child custom content\n\nHTTP GET /custom-content/{id}/children\nPath params:\n  - id (int)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/custom-content/{id}/children'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_page_direct_children(
        self,
        id: int,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        sort: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get direct children of a page\n\nHTTP GET /pages/{id}/direct-children\nPath params:\n  - id (int)\nQuery params:\n  - cursor (str, optional)\n  - limit (int, optional)\n  - sort (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        if sort is not None:
            _query['sort'] = sort
        _body = None
        rel_path = '/pages/{id}/direct-children'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_page_ancestors(
        self,
        id: int,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get all ancestors of page\n\nHTTP GET /pages/{id}/ancestors\nPath params:\n  - id (int)\nQuery params:\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/pages/{id}/ancestors'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_page_descendants(
        self,
        id: int,
        limit: Optional[int] = None,
        depth: Optional[int] = None,
        cursor: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get descendants of page\n\nHTTP GET /pages/{id}/descendants\nPath params:\n  - id (int)\nQuery params:\n  - limit (int, optional)\n  - depth (int, optional)\n  - cursor (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if limit is not None:
            _query['limit'] = limit
        if depth is not None:
            _query['depth'] = depth
        if cursor is not None:
            _query['cursor'] = cursor
        _body = None
        rel_path = '/pages/{id}/descendants'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_bulk_user_lookup(
        self,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create bulk user lookup using ids\n\nHTTP POST /users-bulk\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/users-bulk'
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

    async def check_access_by_email(
        self,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Check site access for a list of emails\n\nHTTP POST /user/access/check-access-by-email\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/user/access/check-access-by-email'
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

    async def invite_by_email(
        self,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Invite a list of emails to the site\n\nHTTP POST /user/access/invite-by-email\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/user/access/invite-by-email'
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

    async def get_data_policy_metadata(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get data policy metadata for the workspace\n\nHTTP GET /data-policies/metadata"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/data-policies/metadata'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_data_policy_spaces(
        self,
        ids: Optional[list[int]] = None,
        keys: Optional[list[str]] = None,
        sort: Optional[Dict[str, Any]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get spaces with data policies\n\nHTTP GET /data-policies/spaces\nQuery params:\n  - ids (list[int], optional)\n  - keys (list[str], optional)\n  - sort (Dict[str, Any], optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if ids is not None:
            _query['ids'] = ids
        if keys is not None:
            _query['keys'] = keys
        if sort is not None:
            _query['sort'] = sort
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/data-policies/spaces'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_classification_levels(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get list of classification levels\n\nHTTP GET /classification-levels"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/classification-levels'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_space_default_classification_level(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get space default classification level\n\nHTTP GET /spaces/{id}/classification-level/default\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/spaces/{id}/classification-level/default'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def put_space_default_classification_level(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update space default classification level\n\nHTTP PUT /spaces/{id}/classification-level/default\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/spaces/{id}/classification-level/default'
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

    async def delete_space_default_classification_level(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete space default classification level\n\nHTTP DELETE /spaces/{id}/classification-level/default\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/spaces/{id}/classification-level/default'
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

    async def get_page_classification_level(
        self,
        id: int,
        status: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get page classification level\n\nHTTP GET /pages/{id}/classification-level\nPath params:\n  - id (int)\nQuery params:\n  - status (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if status is not None:
            _query['status'] = status
        _body = None
        rel_path = '/pages/{id}/classification-level'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def put_page_classification_level(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update page classification level\n\nHTTP PUT /pages/{id}/classification-level\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/pages/{id}/classification-level'
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

    async def post_page_classification_level(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Reset page classification level\n\nHTTP POST /pages/{id}/classification-level/reset\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/pages/{id}/classification-level/reset'
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

    async def get_blog_post_classification_level(
        self,
        id: int,
        status: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get blog post classification level\n\nHTTP GET /blogposts/{id}/classification-level\nPath params:\n  - id (int)\nQuery params:\n  - status (str, optional)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if status is not None:
            _query['status'] = status
        _body = None
        rel_path = '/blogposts/{id}/classification-level'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def put_blog_post_classification_level(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update blog post classification level\n\nHTTP PUT /blogposts/{id}/classification-level\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/blogposts/{id}/classification-level'
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

    async def post_blog_post_classification_level(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Reset blog post classification level\n\nHTTP POST /blogposts/{id}/classification-level/reset\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/blogposts/{id}/classification-level/reset'
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

    async def get_whiteboard_classification_level(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get whiteboard classification level\n\nHTTP GET /whiteboards/{id}/classification-level\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/whiteboards/{id}/classification-level'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def put_whiteboard_classification_level(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update whiteboard classification level\n\nHTTP PUT /whiteboards/{id}/classification-level\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/whiteboards/{id}/classification-level'
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

    async def post_whiteboard_classification_level(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Reset whiteboard classification level\n\nHTTP POST /whiteboards/{id}/classification-level/reset\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/whiteboards/{id}/classification-level/reset'
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

    async def get_database_classification_level(
        self,
        id: int,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get database classification level\n\nHTTP GET /databases/{id}/classification-level\nPath params:\n  - id (int)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/databases/{id}/classification-level'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def put_database_classification_level(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update database classification level\n\nHTTP PUT /databases/{id}/classification-level\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/databases/{id}/classification-level'
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

    async def post_database_classification_level(
        self,
        id: int,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Reset database classification level\n\nHTTP POST /databases/{id}/classification-level/reset\nPath params:\n  - id (int)\nBody: application/json (Any)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/databases/{id}/classification-level/reset'
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

    async def put_forge_app_property(
        self,
        propertyKey: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create or update a Forge app property.\n\nHTTP PUT /app/properties/{propertyKey}\nPath params:\n  - propertyKey (str)\nBody: application/json (Dict[str, Any])"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/app/properties/{propertyKey}'
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

    async def delete_forge_app_property(
        self,
        propertyKey: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Deletes a Forge app property.\n\nHTTP DELETE /app/properties/{propertyKey}\nPath params:\n  - propertyKey (str)"""
        if self._client is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'propertyKey': propertyKey,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/app/properties/{propertyKey}'
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
