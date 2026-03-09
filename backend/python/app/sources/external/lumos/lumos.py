from typing import Any, Dict, Optional, Union
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.lumos.lumos import LumosClient

class LumosDataSource:
    def __init__(self, client: LumosClient) -> None:
        """Default init for the connector-specific data source."""
        self._client = client
        self.http = client.get_client()
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'LumosDataSource':
        return self

    async def list_apps(
        self,
        name_search: Optional[str] = None,
        exact_match: Optional[bool] = None,
        expand: Optional[list[str]] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Apps\n\nHTTP GET /apps\nQuery params:\n  - name_search (str, optional)\n  - exact_match (bool, optional)\n  - expand (str, optional)\n  - page (int, optional)\n  - size (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if name_search is not None:
            _query['name_search'] = name_search
        if exact_match is not None:
            _query['exact_match'] = exact_match
        if expand is not None:
            _query['expand'] = expand
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        _body = None
        rel_path = '/apps'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def create_app(
        self,
        name: str,
        category: str,
        description: str,
        logo_url: Optional[str] = None,
        website_url: Optional[str] = None,
        request_instructions: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create App\n\nHTTP POST /apps\nBody (application/json) fields:\n  - name (str, required)\n  - category (str, required)\n  - description (str, required)\n  - logo_url (str, optional)\n  - website_url (str, optional)\n  - request_instructions (str, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['name'] = name
        _body['category'] = category
        _body['description'] = description
        if logo_url is not None:
            _body['logo_url'] = logo_url
        if website_url is not None:
            _body['website_url'] = website_url
        if request_instructions is not None:
            _body['request_instructions'] = request_instructions
        rel_path = '/apps'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_app_categories(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get App Categories\n\nHTTP GET /apps/categories"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/apps/categories'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_app(
        self,
        app_id: str,
        expand: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get App\n\nHTTP GET /apps/{app_id}\nPath params:\n  - app_id (str)\nQuery params:\n  - expand (str, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'app_id': app_id,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/apps/{app_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def update_app(
        self,
        app_id: str,
        name: str,
        category: str,
        description: str,
        logo_url: Optional[str] = None,
        website_url: Optional[str] = None,
        request_instructions: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update App\n\nHTTP PATCH /apps/{app_id}\nPath params:\n  - app_id (str)\nBody (application/json) fields:\n  - name (str, required)\n  - category (str, required)\n  - description (str, required)\n  - logo_url (str, optional)\n  - website_url (str, optional)\n  - request_instructions (str, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'app_id': app_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['name'] = name
        _body['category'] = category
        _body['description'] = description
        if logo_url is not None:
            _body['logo_url'] = logo_url
        if website_url is not None:
            _body['website_url'] = website_url
        if request_instructions is not None:
            _body['request_instructions'] = request_instructions
        rel_path = '/apps/{app_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PATCH',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_app_settings(
        self,
        app_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Appstore App Settings\n\nHTTP GET /apps/{app_id}/settings\nPath params:\n  - app_id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'app_id': app_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/apps/{app_id}/settings'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def update_app_settings(
        self,
        app_id: str,
        custom_request_instructions: Optional[str] = None,
        request_flow: Optional[Dict[str, Any]] = None,
        provisioning: Optional[Dict[str, Any]] = None,
        in_app_store: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update Domain App Appstore Settings\n\nHTTP PATCH /apps/{app_id}/settings\nPath params:\n  - app_id (str)\nBody (application/json) fields:\n  - custom_request_instructions (str, optional)\n  - request_flow (Dict[str, Any], optional)\n  - provisioning (Dict[str, Any], optional)\n  - in_app_store (bool, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'app_id': app_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if custom_request_instructions is not None:
            _body['custom_request_instructions'] = custom_request_instructions
        if request_flow is not None:
            _body['request_flow'] = request_flow
        if provisioning is not None:
            _body['provisioning'] = provisioning
        if in_app_store is not None:
            _body['in_app_store'] = in_app_store
        rel_path = '/apps/{app_id}/settings'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PATCH',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def current_user(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Current User\n\nHTTP GET /users/current"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/users/current'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def list_users(
        self,
        search_term: Optional[str] = None,
        exact_match: Optional[bool] = None,
        expand: Optional[list[str]] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Users\n\nHTTP GET /users\nQuery params:\n  - search_term (str, optional)\n  - exact_match (bool, optional)\n  - expand (str, optional)\n  - page (int, optional)\n  - size (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if search_term is not None:
            _query['search_term'] = search_term
        if exact_match is not None:
            _query['exact_match'] = exact_match
        if expand is not None:
            _query['expand'] = expand
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        _body = None
        rel_path = '/users'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_user(
        self,
        user_id: str,
        expand: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get User\n\nHTTP GET /users/{user_id}\nPath params:\n  - user_id (str)\nQuery params:\n  - expand (str, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'user_id': user_id,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/users/{user_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_user_accounts(
        self,
        user_id: str,
        expand: Optional[list[str]] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get User Accounts\n\nHTTP GET /users/{user_id}/accounts\nPath params:\n  - user_id (str)\nQuery params:\n  - expand (str, optional)\n  - page (int, optional)\n  - size (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'user_id': user_id,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        _body = None
        rel_path = '/users/{user_id}/accounts'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_inline_webhooks_inline_webhooks_get(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Inline Webhooks\n\nHTTP GET /inline_webhooks"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/inline_webhooks'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_accounts(
        self,
        app_id: Optional[str] = None,
        discovered_before: Optional[str] = None,
        discovered_after: Optional[str] = None,
        sources: Optional[list[str]] = None,
        status: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Accounts\n\nHTTP GET /accounts\nQuery params:\n  - app_id (str, optional)\n  - discovered_before (str, optional)\n  - discovered_after (str, optional)\n  - sources (str, optional)\n  - status (str, optional)\n  - expand (str, optional)\n  - page (int, optional)\n  - size (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if app_id is not None:
            _query['app_id'] = app_id
        if discovered_before is not None:
            _query['discovered_before'] = discovered_before
        if discovered_after is not None:
            _query['discovered_after'] = discovered_after
        if sources is not None:
            _query['sources'] = sources
        if status is not None:
            _query['status'] = status
        if expand is not None:
            _query['expand'] = expand
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        _body = None
        rel_path = '/accounts'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_group_membership(
        self,
        group_id: str,
        page: Optional[int] = None,
        size: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Group Membership\n\nHTTP GET /groups/{group_id}/users\nPath params:\n  - group_id (str)\nQuery params:\n  - page (int, optional)\n  - size (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'group_id': group_id,
        }
        _query: Dict[str, Any] = {}
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        _body = None
        rel_path = '/groups/{group_id}/users'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_group(
        self,
        group_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Group\n\nHTTP GET /groups/{group_id}\nPath params:\n  - group_id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'group_id': group_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/groups/{group_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_groups(
        self,
        integration_specific_id: Optional[str] = None,
        name: Optional[str] = None,
        exact_match: Optional[bool] = None,
        app_id: Optional[str] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Groups\n\nHTTP GET /groups\nQuery params:\n  - integration_specific_id (str, optional)\n  - name (str, optional)\n  - exact_match (bool, optional)\n  - app_id (str, optional)\n  - page (int, optional)\n  - size (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if integration_specific_id is not None:
            _query['integration_specific_id'] = integration_specific_id
        if name is not None:
            _query['name'] = name
        if exact_match is not None:
            _query['exact_match'] = exact_match
        if app_id is not None:
            _query['app_id'] = app_id
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        _body = None
        rel_path = '/groups'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_upload_job_state(
        self,
        job_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Upload Job State\n\nHTTP GET /accounts/upload/{job_id}\nPath params:\n  - job_id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'job_id': job_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/accounts/upload/{job_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_activity_logs(
        self,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Activity Logs\n\nHTTP GET /activity_logs\nQuery params:\n  - since (str, optional)\n  - until (str, optional)\n  - limit (int, optional)\n  - offset (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if since is not None:
            _query['since'] = since
        if until is not None:
            _query['until'] = until
        if limit is not None:
            _query['limit'] = limit
        if offset is not None:
            _query['offset'] = offset
        _body = None
        rel_path = '/activity_logs'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_identity_events(
        self,
        identity_ids: Optional[list[str]] = None,
        changed_fields: Optional[list[str]] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Identity Events\n\nHTTP GET /identity_events\nQuery params:\n  - identity_ids (str, optional)\n  - changed_fields (str, optional)\n  - start_time (str, optional)\n  - end_time (str, optional)\n  - cursor (str, optional)\n  - limit (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if identity_ids is not None:
            _query['identity_ids'] = identity_ids
        if changed_fields is not None:
            _query['changed_fields'] = changed_fields
        if start_time is not None:
            _query['start_time'] = start_time
        if end_time is not None:
            _query['end_time'] = end_time
        if cursor is not None:
            _query['cursor'] = cursor
        if limit is not None:
            _query['limit'] = limit
        _body = None
        rel_path = '/identity_events'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def post_accounts(
        self,
        app_id: str,
        accounts: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create Accounts\n\nHTTP POST /accounts/upload\nBody (application/json) fields:\n  - accounts (list[Dict[str, Any]], optional)\n  - app_id (str, required)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if accounts is not None:
            _body['accounts'] = accounts
        _body['app_id'] = app_id
        rel_path = '/accounts/upload'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def activity_records(
        self,
        records: list[Dict[str, Any]],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update Activity Records\n\nHTTP POST /activity_records\nBody (application/json) fields:\n  - records (list[Dict[str, Any]], required)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['records'] = records
        rel_path = '/activity_records'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_activity_records_job_state(
        self,
        job_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Activity Records Job State\n\nHTTP GET /activity_records/job/{job_id}\nPath params:\n  - job_id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'job_id': job_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/activity_records/job/{job_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_user_roles_users_user_id_roles_get(
        self,
        user_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get User Roles\n\nHTTP GET /users/{user_id}/roles\nPath params:\n  - user_id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'user_id': user_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/users/{user_id}/roles'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def add_role_to_user_users_user_id_roles_role_name_post(
        self,
        user_id: str,
        role_name: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add Role To User\n\nHTTP POST /users/{user_id}/roles/{role_name}\nPath params:\n  - user_id (str)\n  - role_name (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'user_id': user_id,
            'role_name': role_name,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/users/{user_id}/roles/{role_name}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def remove_role_from_user_users_user_id_roles_role_name_delete(
        self,
        user_id: str,
        role_name: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove Role From User\n\nHTTP DELETE /users/{user_id}/roles/{role_name}\nPath params:\n  - user_id (str)\n  - role_name (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'user_id': user_id,
            'role_name': role_name,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/users/{user_id}/roles/{role_name}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_appstore_permissions_for_app_appstore_apps_app_id_requestable_permissions_get(
        self,
        app_id: str,
        search_term: Optional[str] = None,
        exact_match: Optional[bool] = None,
        in_app_store: Optional[bool] = None,
        include_inherited_configs: Optional[bool] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Appstore Permissions For App\n\nHTTP GET /appstore/apps/{app_id}/requestable_permissions\nPath params:\n  - app_id (str)\nQuery params:\n  - search_term (str, optional)\n  - exact_match (bool, optional)\n  - in_app_store (str, optional)\n  - include_inherited_configs (bool, optional)\n  - page (int, optional)\n  - size (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'app_id': app_id,
        }
        _query: Dict[str, Any] = {}
        if search_term is not None:
            _query['search_term'] = search_term
        if exact_match is not None:
            _query['exact_match'] = exact_match
        if in_app_store is not None:
            _query['in_app_store'] = in_app_store
        if include_inherited_configs is not None:
            _query['include_inherited_configs'] = include_inherited_configs
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        _body = None
        rel_path = '/appstore/apps/{app_id}/requestable_permissions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_appstore_permissions_appstore_requestable_permissions_get(
        self,
        app_id: Optional[str] = None,
        search_term: Optional[str] = None,
        exact_match: Optional[bool] = None,
        in_app_store: Optional[bool] = None,
        include_inherited_configs: Optional[bool] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Appstore Permissions\n\nHTTP GET /appstore/requestable_permissions\nQuery params:\n  - app_id (str, optional)\n  - search_term (str, optional)\n  - exact_match (bool, optional)\n  - in_app_store (str, optional)\n  - include_inherited_configs (bool, optional)\n  - page (int, optional)\n  - size (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if app_id is not None:
            _query['app_id'] = app_id
        if search_term is not None:
            _query['search_term'] = search_term
        if exact_match is not None:
            _query['exact_match'] = exact_match
        if in_app_store is not None:
            _query['in_app_store'] = in_app_store
        if include_inherited_configs is not None:
            _query['include_inherited_configs'] = include_inherited_configs
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        _body = None
        rel_path = '/appstore/requestable_permissions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def create_appstore_requestable_permission_appstore_requestable_permissions_post(
        self,
        app_id: str,
        label: str,
        include_inherited_configs: Optional[bool] = None,
        app_class_id: Optional[str] = None,
        app_instance_id: Optional[str] = None,
        request_config: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create Appstore Requestable Permission\n\nHTTP POST /appstore/requestable_permissions\nQuery params:\n  - include_inherited_configs (bool, optional)\nBody (application/json) fields:\n  - app_id (str, required)\n  - app_class_id (str, optional)\n  - app_instance_id (str, optional)\n  - label (str, required)\n  - request_config (str, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if include_inherited_configs is not None:
            _query['include_inherited_configs'] = include_inherited_configs
        _body: Dict[str, Any] = {}
        _body['app_id'] = app_id
        if app_class_id is not None:
            _body['app_class_id'] = app_class_id
        if app_instance_id is not None:
            _body['app_instance_id'] = app_instance_id
        _body['label'] = label
        if request_config is not None:
            _body['request_config'] = request_config
        rel_path = '/appstore/requestable_permissions'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_appstore_permission_appstore_requestable_permissions_permission_id_get(
        self,
        permission_id: str,
        include_inherited_configs: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Appstore Permission\n\nHTTP GET /appstore/requestable_permissions/{permission_id}\nPath params:\n  - permission_id (str)\nQuery params:\n  - include_inherited_configs (bool, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'permission_id': permission_id,
        }
        _query: Dict[str, Any] = {}
        if include_inherited_configs is not None:
            _query['include_inherited_configs'] = include_inherited_configs
        _body = None
        rel_path = '/appstore/requestable_permissions/{permission_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def update_appstore_permission_appstore_requestable_permissions_permission_id_patch(
        self,
        permission_id: str,
        include_inherited_configs: Optional[bool] = None,
        app_id: Optional[str] = None,
        app_class_id: Optional[str] = None,
        app_instance_id: Optional[str] = None,
        label: Optional[str] = None,
        request_config: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update Appstore Permission\n\nHTTP PATCH /appstore/requestable_permissions/{permission_id}\nPath params:\n  - permission_id (str)\nQuery params:\n  - include_inherited_configs (bool, optional)\nBody (application/json) fields:\n  - app_id (str, optional)\n  - app_class_id (str, optional)\n  - app_instance_id (str, optional)\n  - label (str, optional)\n  - request_config (str, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'permission_id': permission_id,
        }
        _query: Dict[str, Any] = {}
        if include_inherited_configs is not None:
            _query['include_inherited_configs'] = include_inherited_configs
        _body: Dict[str, Any] = {}
        if app_id is not None:
            _body['app_id'] = app_id
        if app_class_id is not None:
            _body['app_class_id'] = app_class_id
        if app_instance_id is not None:
            _body['app_instance_id'] = app_instance_id
        if label is not None:
            _body['label'] = label
        if request_config is not None:
            _body['request_config'] = request_config
        rel_path = '/appstore/requestable_permissions/{permission_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PATCH',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def delete_appstore_permission_appstore_requestable_permissions_permission_id_delete(
        self,
        permission_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete Appstore Permission\n\nHTTP DELETE /appstore/requestable_permissions/{permission_id}\nPath params:\n  - permission_id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'permission_id': permission_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/appstore/requestable_permissions/{permission_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_appstore_pre_approval_rules_for_app_appstore_pre_approval_rules_get(
        self,
        app_id: Optional[str] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Appstore Pre Approval Rules For App\n\nHTTP GET /appstore/pre_approval_rules\nQuery params:\n  - app_id (str, optional)\n  - page (int, optional)\n  - size (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if app_id is not None:
            _query['app_id'] = app_id
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        _body = None
        rel_path = '/appstore/pre_approval_rules'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def create_pre_approval_rule_appstore_pre_approval_rules_post(
        self,
        justification: str,
        app_id: str,
        time_based_access: Optional[list[Dict[str, Any]]] = None,
        preapproved_groups: Optional[list[Dict[str, Any]]] = None,
        preapproved_permissions: Optional[list[Dict[str, Any]]] = None,
        preapproved_users_by_attribute: Optional[list[Dict[str, Any]]] = None,
        preapproval_webhooks: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create Pre Approval Rule\n\nHTTP POST /appstore/pre_approval_rules\nBody (application/json) fields:\n  - justification (str, required)\n  - time_based_access (list[Dict[str, Any]], optional)\n  - app_id (str, required)\n  - preapproved_groups (list[Dict[str, Any]], optional)\n  - preapproved_permissions (list[Dict[str, Any]], optional)\n  - preapproved_users_by_attribute (list[Dict[str, Any]], optional)\n  - preapproval_webhooks (list[Dict[str, Any]], optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['justification'] = justification
        if time_based_access is not None:
            _body['time_based_access'] = time_based_access
        _body['app_id'] = app_id
        if preapproved_groups is not None:
            _body['preapproved_groups'] = preapproved_groups
        if preapproved_permissions is not None:
            _body['preapproved_permissions'] = preapproved_permissions
        if preapproved_users_by_attribute is not None:
            _body['preapproved_users_by_attribute'] = preapproved_users_by_attribute
        if preapproval_webhooks is not None:
            _body['preapproval_webhooks'] = preapproval_webhooks
        rel_path = '/appstore/pre_approval_rules'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_appstore_pre_approval_rule_appstore_pre_approval_rules_pre_approval_rule_id_get(
        self,
        pre_approval_rule_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Appstore Pre Approval Rule\n\nHTTP GET /appstore/pre_approval_rules/{pre_approval_rule_id}\nPath params:\n  - pre_approval_rule_id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'pre_approval_rule_id': pre_approval_rule_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/appstore/pre_approval_rules/{pre_approval_rule_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def update_pre_approval_rule_appstore_pre_approval_rules_pre_approval_rule_id_patch(
        self,
        pre_approval_rule_id: str,
        justification: str,
        time_based_access: Optional[list[Dict[str, Any]]] = None,
        preapproved_groups: Optional[list[Dict[str, Any]]] = None,
        preapproved_permissions: Optional[list[Dict[str, Any]]] = None,
        preapproved_users_by_attribute: Optional[list[Dict[str, Any]]] = None,
        preapproval_webhooks: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update Pre Approval Rule\n\nHTTP PATCH /appstore/pre_approval_rules/{pre_approval_rule_id}\nPath params:\n  - pre_approval_rule_id (str)\nBody (application/json) fields:\n  - justification (str, required)\n  - time_based_access (list[Dict[str, Any]], optional)\n  - preapproved_groups (list[Dict[str, Any]], optional)\n  - preapproved_permissions (list[Dict[str, Any]], optional)\n  - preapproved_users_by_attribute (list[Dict[str, Any]], optional)\n  - preapproval_webhooks (list[Dict[str, Any]], optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'pre_approval_rule_id': pre_approval_rule_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['justification'] = justification
        if time_based_access is not None:
            _body['time_based_access'] = time_based_access
        if preapproved_groups is not None:
            _body['preapproved_groups'] = preapproved_groups
        if preapproved_permissions is not None:
            _body['preapproved_permissions'] = preapproved_permissions
        if preapproved_users_by_attribute is not None:
            _body['preapproved_users_by_attribute'] = preapproved_users_by_attribute
        if preapproval_webhooks is not None:
            _body['preapproval_webhooks'] = preapproval_webhooks
        rel_path = '/appstore/pre_approval_rules/{pre_approval_rule_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PATCH',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def delete_pre_approval_rule_appstore_pre_approval_rules_pre_approval_rule_id_delete(
        self,
        pre_approval_rule_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete Pre Approval Rule\n\nHTTP DELETE /appstore/pre_approval_rules/{pre_approval_rule_id}\nPath params:\n  - pre_approval_rule_id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'pre_approval_rule_id': pre_approval_rule_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/appstore/pre_approval_rules/{pre_approval_rule_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_app_store_app_settings(
        self,
        app_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Appstore App Settings\n\nHTTP GET /appstore/apps/{app_id}/settings\nPath params:\n  - app_id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'app_id': app_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/appstore/apps/{app_id}/settings'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def update_app_store_app_settings(
        self,
        app_id: str,
        custom_request_instructions: Optional[str] = None,
        request_flow: Optional[Dict[str, Any]] = None,
        provisioning: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update Appstore App Settings\n\nHTTP PATCH /appstore/apps/{app_id}/settings\nPath params:\n  - app_id (str)\nBody (application/json) fields:\n  - custom_request_instructions (str, optional)\n  - request_flow (Dict[str, Any], optional)\n  - provisioning (Dict[str, Any], optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'app_id': app_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if custom_request_instructions is not None:
            _body['custom_request_instructions'] = custom_request_instructions
        if request_flow is not None:
            _body['request_flow'] = request_flow
        if provisioning is not None:
            _body['provisioning'] = provisioning
        rel_path = '/appstore/apps/{app_id}/settings'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PATCH',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def add_app_to_app_store(
        self,
        app_id: str,
        custom_request_instructions: Optional[str] = None,
        request_flow: Optional[Dict[str, Any]] = None,
        provisioning: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Add App To Appstore\n\nHTTP POST /appstore/apps\nBody (application/json) fields:\n  - custom_request_instructions (str, optional)\n  - request_flow (Dict[str, Any], optional)\n  - provisioning (Dict[str, Any], optional)\n  - app_id (str, required)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if custom_request_instructions is not None:
            _body['custom_request_instructions'] = custom_request_instructions
        if request_flow is not None:
            _body['request_flow'] = request_flow
        if provisioning is not None:
            _body['provisioning'] = provisioning
        _body['app_id'] = app_id
        rel_path = '/appstore/apps'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_app_store_apps(
        self,
        app_id: Optional[str] = None,
        name_search: Optional[str] = None,
        exact_match: Optional[bool] = None,
        all_visibilities: Optional[bool] = None,
        expand: Optional[list[str]] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Appstore Apps\n\nHTTP GET /appstore/apps\nQuery params:\n  - app_id (str, optional)\n  - name_search (str, optional)\n  - exact_match (bool, optional)\n  - all_visibilities (bool, optional)\n  - expand (str, optional)\n  - page (int, optional)\n  - size (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if app_id is not None:
            _query['app_id'] = app_id
        if name_search is not None:
            _query['name_search'] = name_search
        if exact_match is not None:
            _query['exact_match'] = exact_match
        if all_visibilities is not None:
            _query['all_visibilities'] = all_visibilities
        if expand is not None:
            _query['expand'] = expand
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        _body = None
        rel_path = '/appstore/apps'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def remove_app_from_app_store(
        self,
        app_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Remove App From Appstore\n\nHTTP DELETE /appstore/apps/{app_id}\nPath params:\n  - app_id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'app_id': app_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/appstore/apps/{app_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_app_store_app(
        self,
        app_id: str,
        expand: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Appstore App\n\nHTTP GET /appstore/apps/{app_id}\nPath params:\n  - app_id (str)\nQuery params:\n  - expand (str, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'app_id': app_id,
        }
        _query: Dict[str, Any] = {}
        if expand is not None:
            _query['expand'] = expand
        _body = None
        rel_path = '/appstore/apps/{app_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def cancel_access_request(
        self,
        id: str,
        reason: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Cancel Access Request\n\nHTTP DELETE /appstore/access_requests/{id}\nPath params:\n  - id (str)\nQuery params:\n  - reason (str, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        if reason is not None:
            _query['reason'] = reason
        _body = None
        rel_path = '/appstore/access_requests/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_access_request(
        self,
        id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Access Request\n\nHTTP GET /appstore/access_requests/{id}\nPath params:\n  - id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'id': id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/appstore/access_requests/{id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def create_access_request(
        self,
        app_id: str,
        requester_user_id: Optional[str] = None,
        target_user_id: Optional[str] = None,
        note: Optional[str] = None,
        business_justification: Optional[str] = None,
        expiration_in_seconds: Optional[int] = None,
        access_length: Optional[str] = None,
        requestable_permission_ids: Optional[list[str]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create Access Request\n\nHTTP POST /appstore/access_request\nBody (application/json) fields:\n  - app_id (str, required)\n  - requester_user_id (str, optional)\n  - target_user_id (str, optional)\n  - note (str, optional)\n  - business_justification (str, optional)\n  - expiration_in_seconds (str, optional)\n  - access_length (str, optional)\n  - requestable_permission_ids (str, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['app_id'] = app_id
        if requester_user_id is not None:
            _body['requester_user_id'] = requester_user_id
        if target_user_id is not None:
            _body['target_user_id'] = target_user_id
        if note is not None:
            _body['note'] = note
        if business_justification is not None:
            _body['business_justification'] = business_justification
        if expiration_in_seconds is not None:
            _body['expiration_in_seconds'] = expiration_in_seconds
        if access_length is not None:
            _body['access_length'] = access_length
        if requestable_permission_ids is not None:
            _body['requestable_permission_ids'] = requestable_permission_ids
        rel_path = '/appstore/access_request'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_access_requests(
        self,
        target_user_id: Optional[str] = None,
        requester_user_id: Optional[str] = None,
        user_id: Optional[str] = None,
        statuses: Optional[list[str]] = None,
        sort: Optional[str] = None,
        page: Optional[int] = None,
        size: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Access Requests\n\nHTTP GET /appstore/access_requests\nQuery params:\n  - target_user_id (str, optional)\n  - requester_user_id (str, optional)\n  - user_id (str, optional)\n  - statuses (str, optional)\n  - sort (str, optional)\n  - page (int, optional)\n  - size (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if target_user_id is not None:
            _query['target_user_id'] = target_user_id
        if requester_user_id is not None:
            _query['requester_user_id'] = requester_user_id
        if user_id is not None:
            _query['user_id'] = user_id
        if statuses is not None:
            _query['statuses'] = statuses
        if sort is not None:
            _query['sort'] = sort
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        _body = None
        rel_path = '/appstore/access_requests'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_access_policies(
        self,
        page: Optional[int] = None,
        size: Optional[int] = None,
        name: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Access Policies\n\nHTTP GET /access_policies\nQuery params:\n  - page (int, optional)\n  - size (int, optional)\n  - name (str, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        if name is not None:
            _query['name'] = name
        _body = None
        rel_path = '/access_policies'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def create_access_policy(
        self,
        name: str,
        business_justification: str,
        apps: list[Dict[str, Any]],
        access_condition: Optional[str] = None,
        is_everyone_condition: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Create Access Policy\n\nHTTP POST /access_policies\nBody (application/json) fields:\n  - name (str, required)\n  - business_justification (str, required)\n  - apps (list[Dict[str, Any]], required)\n  - access_condition (str, optional)\n  - is_everyone_condition (bool, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['name'] = name
        _body['business_justification'] = business_justification
        _body['apps'] = apps
        if access_condition is not None:
            _body['access_condition'] = access_condition
        if is_everyone_condition is not None:
            _body['is_everyone_condition'] = is_everyone_condition
        rel_path = '/access_policies'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def get_access_policy(
        self,
        access_policy_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Access Policy\n\nHTTP GET /access_policies/{access_policy_id}\nPath params:\n  - access_policy_id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'access_policy_id': access_policy_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/access_policies/{access_policy_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def delete_access_policy(
        self,
        access_policy_id: str,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Delete Access Policy\n\nHTTP DELETE /access_policies/{access_policy_id}\nPath params:\n  - access_policy_id (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {
            'access_policy_id': access_policy_id,
        }
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/access_policies/{access_policy_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='DELETE',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def update_access_policy(
        self,
        access_policy_id: str,
        name: str,
        business_justification: str,
        apps: list[Dict[str, Any]],
        access_condition: Optional[str] = None,
        is_everyone_condition: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update Access Policy\n\nHTTP PUT /access_policies/{access_policy_id}\nPath params:\n  - access_policy_id (str)\nBody (application/json) fields:\n  - name (str, required)\n  - business_justification (str, required)\n  - apps (list[Dict[str, Any]], required)\n  - access_condition (str, optional)\n  - is_everyone_condition (bool, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'access_policy_id': access_policy_id,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['name'] = name
        _body['business_justification'] = business_justification
        _body['apps'] = apps
        if access_condition is not None:
            _body['access_condition'] = access_condition
        if is_everyone_condition is not None:
            _body['is_everyone_condition'] = is_everyone_condition
        rel_path = '/access_policies/{access_policy_id}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def list_vendor_agreements(
        self,
        page: Optional[int] = None,
        size: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Vendor Agreements\n\nHTTP GET /vendor_agreements\nQuery params:\n  - page (int, optional)\n  - size (int, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        if page is not None:
            _query['page'] = page
        if size is not None:
            _query['size'] = size
        _body = None
        rel_path = '/vendor_agreements'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def update_vendor_agreement_custom_attribute(
        self,
        vendor_agreement_id: str,
        label: str,
        value: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Update Vendor Agreement Custom Attribute\n\nHTTP PUT /vendor_agreements/{vendor_agreement_id}/custom_attributes/{label}\nPath params:\n  - vendor_agreement_id (str)\n  - label (str)\nBody (application/json) fields:\n  - value (str, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'vendor_agreement_id': vendor_agreement_id,
            'label': label,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        if value is not None:
            _body['value'] = value
        rel_path = '/vendor_agreements/{vendor_agreement_id}/custom_attributes/{label}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='PUT',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def create_found_document(
        self,
        unique_identifier: str,
        files: list[Dict[str, Any]],
        vendor_name: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        source_app_id: Optional[str] = None,
        total_cost: Optional[Dict[str, Any]] = None,
        line_items: Optional[list[Dict[str, Any]]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Upload Found Documents\n\nHTTP POST /found_documents\nBody (application/json) fields:\n  - unique_identifier (str, required)\n  - files (list[Dict[str, Any]], required)\n  - vendor_name (str, optional)\n  - start_date (str, optional)\n  - end_date (str, optional)\n  - source_app_id (str, optional)\n  - total_cost (Dict[str, Any], optional)\n  - line_items (list[Dict[str, Any]], optional) - Line items on the contract (each with name, quantity, unit_cost)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['unique_identifier'] = unique_identifier
        _body['files'] = files
        if vendor_name is not None:
            _body['vendor_name'] = vendor_name
        if start_date is not None:
            _body['start_date'] = start_date
        if end_date is not None:
            _body['end_date'] = end_date
        if source_app_id is not None:
            _body['source_app_id'] = source_app_id
        if total_cost is not None:
            _body['total_cost'] = total_cost
        if line_items is not None:
            _body['line_items'] = line_items
        rel_path = '/found_documents'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def create_order(
        self,
        unique_identifier: str,
        vendor: Dict[str, Any],
        start_date: str,
        end_date: str,
        auto_renewal: bool,
        line_items: list[Dict[str, Any]],
        opt_out_date: Optional[str] = None,
        currency: Optional[str] = None,
        custom_attributes: Optional[Dict[str, Any]] = None,
        source_app_id: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Upload Order\n\nHTTP POST /orders\nBody (application/json) fields:\n  - unique_identifier (str, required)\n  - vendor (Dict[str, Any], required)\n  - start_date (str, required)\n  - end_date (str, required)\n  - opt_out_date (str, optional)\n  - auto_renewal (bool, required)\n  - currency (str, optional)\n  - line_items (list[Dict[str, Any]], required)\n  - custom_attributes (Dict[str, Any], optional)\n  - source_app_id (str, optional)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['unique_identifier'] = unique_identifier
        _body['vendor'] = vendor
        _body['start_date'] = start_date
        _body['end_date'] = end_date
        if opt_out_date is not None:
            _body['opt_out_date'] = opt_out_date
        _body['auto_renewal'] = auto_renewal
        if currency is not None:
            _body['currency'] = currency
        _body['line_items'] = line_items
        if custom_attributes is not None:
            _body['custom_attributes'] = custom_attributes
        if source_app_id is not None:
            _body['source_app_id'] = source_app_id
        rel_path = '/orders'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def lumos_art(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Art\n\nHTTP GET /art"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/art'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def lumos_liveness_check(
        self,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Get Info\n\nHTTP GET /info"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {}
        _query: Dict[str, Any] = {}
        _body = None
        rel_path = '/info'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='GET',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def process_airbase_milestone_event(
        self,
        domain_app_uuid: str,
        id: str,
        object: str,
        type: str,
        created_date: str,
        data: Dict[str, Any],
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Process Airbase Purchase Request Approved\n\nHTTP POST /webhooks/airbase/purchase_request_approved/{domain_app_uuid}\nPath params:\n  - domain_app_uuid (str)\nBody (application/json) fields:\n  - id (str, required)\n  - object (str, required)\n  - type (str, required)\n  - created_date (str, required)\n  - data (Dict[str, Any], required)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'domain_app_uuid': domain_app_uuid,
        }
        _query: Dict[str, Any] = {}
        _body: Dict[str, Any] = {}
        _body['id'] = id
        _body['object'] = object
        _body['type'] = type
        _body['created_date'] = created_date
        _body['data'] = data
        rel_path = '/webhooks/airbase/purchase_request_approved/{domain_app_uuid}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
        return resp

    async def process_vendr_request_completed_webhooks_vendr_request_completed_domain_app_uuid_post(
        self,
        domain_app_uuid: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Auto-generated from OpenAPI: Process Vendr Request Completed\n\nHTTP POST /webhooks/vendr/request_completed/{domain_app_uuid}\nPath params:\n  - domain_app_uuid (str)\nBody: application/json (str)"""
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault('Content-Type', 'application/json')
        _path: Dict[str, Any] = {
            'domain_app_uuid': domain_app_uuid,
        }
        _query: Dict[str, Any] = {}
        _body = body
        rel_path = '/webhooks/vendr/request_completed/{domain_app_uuid}'
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method='POST',
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self.http.execute(req)
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

def _serialize_value(v: Any) -> str:
    if v is None:
        return ''
    if isinstance(v, (list, tuple, set)):
        return ','.join(_to_bool_str(x) for x in v)
    return _to_bool_str(v)

def _as_str_dict(d: Dict[str, Any]) -> Dict[str, str]:
    return {str(k): _serialize_value(v) for k, v in (d or {}).items()}
