from typing import Dict, List, Optional

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.zammad.zammad import ZammadClient, ZammadResponse

SUCCESS_CODE_IS_LESS_THAN = 400


class ZammadDataSource:
    """Complete Zammad API - 200+ methods covering all endpoints."""

    def __init__(self, zammad_client: ZammadClient) -> None:
        """Initialize with ZammadClient."""
        self.http_client = zammad_client.get_client()
        self._zammad_client = zammad_client
        self.base_url = zammad_client.get_base_url().rstrip('/')

    def get_client(self) -> ZammadClient:
        """Get ZammadClient."""
        return self._zammad_client

    async def cti_new_call(
        self,
        token: str,
        event: str,
        from_number: str,
        to: str,
        direction: str,
        call_id: str,
        user: Optional[List[str]] = None
    ) -> ZammadResponse:
        """CTI new call event from PBX

        Args:
            token: str (required)
            event: str (required)
            from_number: str (required)
            to: str (required)
            direction: str (required)
            call_id: str (required)
            user: Optional[List[str]] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/cti/{token}"
        request_body: Dict = {}
        if event is not None:
            request_body["event"] = event
        if from_number is not None:
            request_body["from"] = from_number
        if to is not None:
            request_body["to"] = to
        if direction is not None:
            request_body["direction"] = direction
        if call_id is not None:
            request_body["call_id"] = call_id
        if user is not None:
            request_body["user"] = user

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="cti_new_call succeeded" if status_ok else "cti_new_call failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="cti_new_call failed: " + str(e)
            )

    async def cti_answer(
        self,
        token: str,
        event: str,
        call_id: str,
        user: str,
        from_number: str,
        to: str,
        direction: str,
        answering_number: str
    ) -> ZammadResponse:
        """CTI call answered event

        Args:
            token: str (required)
            event: str (required)
            call_id: str (required)
            user: str (required)
            from_number: str (required)
            to: str (required)
            direction: str (required)
            answering_number: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/cti/{token}"
        request_body: Dict = {}
        if event is not None:
            request_body["event"] = event
        if call_id is not None:
            request_body["call_id"] = call_id
        if user is not None:
            request_body["user"] = user
        if from_number is not None:
            request_body["from"] = from_number
        if to is not None:
            request_body["to"] = to
        if direction is not None:
            request_body["direction"] = direction
        if answering_number is not None:
            request_body["answering_number"] = answering_number

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="cti_answer succeeded" if status_ok else "cti_answer failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="cti_answer failed: " + str(e)
            )

    async def cti_hangup(
        self,
        token: str,
        event: str,
        call_id: str,
        cause: str,
        from_number: str,
        to: str,
        direction: str,
        answering_number: Optional[str] = None
    ) -> ZammadResponse:
        """CTI call hangup event

        Args:
            token: str (required)
            event: str (required)
            call_id: str (required)
            cause: str (required)
            from_number: str (required)
            to: str (required)
            direction: str (required)
            answering_number: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/cti/{token}"
        request_body: Dict = {}
        if event is not None:
            request_body["event"] = event
        if call_id is not None:
            request_body["call_id"] = call_id
        if cause is not None:
            request_body["cause"] = cause
        if from_number is not None:
            request_body["from"] = from_number
        if to is not None:
            request_body["to"] = to
        if direction is not None:
            request_body["direction"] = direction
        if answering_number is not None:
            request_body["answering_number"] = answering_number

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="cti_hangup succeeded" if status_ok else "cti_hangup failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="cti_hangup failed: " + str(e)
            )

    async def list_cti_logs(
        self
    ) -> ZammadResponse:
        """List CTI call logs

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/cti/log"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_cti_logs succeeded" if status_ok else "list_cti_logs failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_cti_logs failed: " + str(e)
            )

    async def list_schedulers(
        self
    ) -> ZammadResponse:
        """List all schedulers

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/schedulers"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_schedulers succeeded" if status_ok else "list_schedulers failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_schedulers failed: " + str(e)
            )

    async def get_scheduler(
        self,
        id: int
    ) -> ZammadResponse:
        """Get scheduler by ID

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/schedulers/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_scheduler succeeded" if status_ok else "get_scheduler failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_scheduler failed: " + str(e)
            )

    async def create_scheduler(
        self,
        name: str,
        timeplan: Dict,
        condition: Dict,
        perform: Dict,
        object_name: str,
        note: Optional[str] = None,
        active: Optional[bool] = None
    ) -> ZammadResponse:
        """Create scheduler

        Args:
            name: str (required)
            timeplan: Dict (required)
            condition: Dict (required)
            perform: Dict (required)
            object_name: str (required)
            note: Optional[str] (optional)
            active: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/schedulers"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if timeplan is not None:
            request_body["timeplan"] = timeplan
        if condition is not None:
            request_body["condition"] = condition
        if perform is not None:
            request_body["perform"] = perform
        if object_name is not None:
            request_body["object"] = object_name
        if note is not None:
            request_body["note"] = note
        if active is not None:
            request_body["active"] = active

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_scheduler succeeded" if status_ok else "create_scheduler failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_scheduler failed: " + str(e)
            )

    async def update_scheduler(
        self,
        id: int,
        name: Optional[str] = None,
        timeplan: Optional[Dict] = None,
        condition: Optional[Dict] = None,
        perform: Optional[Dict] = None,
        object_name: Optional[str] = None,
        note: Optional[str] = None,
        active: Optional[bool] = None
    ) -> ZammadResponse:
        """Update scheduler

        Args:
            id: int (required)
            name: Optional[str] (optional)
            timeplan: Optional[Dict] (optional)
            condition: Optional[Dict] (optional)
            perform: Optional[Dict] (optional)
            object_name: Optional[str] (optional)
            note: Optional[str] (optional)
            active: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/schedulers/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if timeplan is not None:
            request_body["timeplan"] = timeplan
        if condition is not None:
            request_body["condition"] = condition
        if perform is not None:
            request_body["perform"] = perform
        if object_name is not None:
            request_body["object"] = object_name
        if note is not None:
            request_body["note"] = note
        if active is not None:
            request_body["active"] = active

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_scheduler succeeded" if status_ok else "update_scheduler failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_scheduler failed: " + str(e)
            )

    async def delete_scheduler(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete scheduler

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/schedulers/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_scheduler succeeded" if status_ok else "delete_scheduler failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_scheduler failed: " + str(e)
            )

    async def list_chat_sessions(
        self
    ) -> ZammadResponse:
        """List chat sessions

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/chats"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_chat_sessions succeeded" if status_ok else "list_chat_sessions failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_chat_sessions failed: " + str(e)
            )

    async def get_chat_session(
        self,
        id: int
    ) -> ZammadResponse:
        """Get chat session

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/chats/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_chat_session succeeded" if status_ok else "get_chat_session failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_chat_session failed: " + str(e)
            )

    async def search_chat_sessions(
        self,
        query: str,
        limit: Optional[int] = None
    ) -> ZammadResponse:
        """Search chat sessions

        Args:
            query: str (required)
            limit: Optional[int] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/chats/search"
        params = {}
        if query is not None:
            params["query"] = query
        if limit is not None:
            params["limit"] = limit
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="search_chat_sessions succeeded" if status_ok else "search_chat_sessions failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="search_chat_sessions failed: " + str(e)
            )

    async def init_knowledge_base(
        self
    ) -> ZammadResponse:
        """Initialize knowledge base

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/init"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="init_knowledge_base succeeded" if status_ok else "init_knowledge_base failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="init_knowledge_base failed: " + str(e)
            )

    async def get_knowledge_base(
        self,
        id: int
    ) -> ZammadResponse:
        """Get knowledge base

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_knowledge_base succeeded" if status_ok else "get_knowledge_base failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_knowledge_base failed: " + str(e)
            )

    async def update_knowledge_base(
        self,
        id: int,
        iconset: Optional[str] = None,
        color_highlight: Optional[str] = None,
        color_header: Optional[str] = None,
        custom_address: Optional[str] = None
    ) -> ZammadResponse:
        """Update knowledge base

        Args:
            id: int (required)
            iconset: Optional[str] (optional)
            color_highlight: Optional[str] (optional)
            color_header: Optional[str] (optional)
            custom_address: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/{id}"
        request_body: Dict = {}
        if iconset is not None:
            request_body["iconset"] = iconset
        if color_highlight is not None:
            request_body["color_highlight"] = color_highlight
        if color_header is not None:
            request_body["color_header"] = color_header
        if custom_address is not None:
            request_body["custom_address"] = custom_address

        try:
            request = HTTPRequest(
                url=url,
                method="PATCH",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_knowledge_base succeeded" if status_ok else "update_knowledge_base failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_knowledge_base failed: " + str(e)
            )

    async def list_kb_categories(
        self,
        kb_id: int
    ) -> ZammadResponse:
        """List KB categories

        Args:
            kb_id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/{kb_id}/categories"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_kb_categories succeeded" if status_ok else "list_kb_categories failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_kb_categories failed: " + str(e)
            )

    async def get_kb_category(
        self,
        kb_id: int,
        id: int
    ) -> ZammadResponse:
        """Get KB category

        Args:
            kb_id: int (required)
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/{kb_id}/categories/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_kb_category succeeded" if status_ok else "get_kb_category failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_kb_category failed: " + str(e)
            )

    async def create_kb_category(
        self,
        kb_id: int,
        knowledge_base_id: int,
        translations: Dict,
        parent_id: Optional[int] = None,
        category_icon: Optional[str] = None
    ) -> ZammadResponse:
        """Create KB category

        Args:
            kb_id: int (required)
            knowledge_base_id: int (required)
            translations: Dict (required)
            parent_id: Optional[int] (optional)
            category_icon: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/{kb_id}/categories"
        request_body: Dict = {}
        if knowledge_base_id is not None:
            request_body["knowledge_base_id"] = knowledge_base_id
        if translations is not None:
            request_body["translations"] = translations
        if parent_id is not None:
            request_body["parent_id"] = parent_id
        if category_icon is not None:
            request_body["category_icon"] = category_icon

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_kb_category succeeded" if status_ok else "create_kb_category failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_kb_category failed: " + str(e)
            )

    async def update_kb_category(
        self,
        kb_id: int,
        id: int,
        parent_id: Optional[int] = None,
        category_icon: Optional[str] = None,
        translations: Optional[Dict] = None
    ) -> ZammadResponse:
        """Update KB category

        Args:
            kb_id: int (required)
            id: int (required)
            parent_id: Optional[int] (optional)
            category_icon: Optional[str] (optional)
            translations: Optional[Dict] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/{kb_id}/categories/{id}"
        request_body: Dict = {}
        if parent_id is not None:
            request_body["parent_id"] = parent_id
        if category_icon is not None:
            request_body["category_icon"] = category_icon
        if translations is not None:
            request_body["translations"] = translations

        try:
            request = HTTPRequest(
                url=url,
                method="PATCH",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_kb_category succeeded" if status_ok else "update_kb_category failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_kb_category failed: " + str(e)
            )

    async def delete_kb_category(
        self,
        kb_id: int,
        id: int
    ) -> ZammadResponse:
        """Delete KB category

        Args:
            kb_id: int (required)
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/{kb_id}/categories/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_kb_category succeeded" if status_ok else "delete_kb_category failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_kb_category failed: " + str(e)
            )

    async def reorder_kb_categories(
        self,
        kb_id: int,
        category_ids: List[int]
    ) -> ZammadResponse:
        """Reorder KB categories

        Args:
            kb_id: int (required)
            category_ids: List[int] (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/{kb_id}/categories/reorder"
        request_body: Dict = {}
        if category_ids is not None:
            request_body["category_ids"] = category_ids

        try:
            request = HTTPRequest(
                url=url,
                method="PATCH",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="reorder_kb_categories succeeded" if status_ok else "reorder_kb_categories failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="reorder_kb_categories failed: " + str(e)
            )

    async def list_kb_answers(
        self,
        category_id: Optional[int] = None
    ) -> ZammadResponse:
        """List KB answers

        Args:
            category_id: Optional[int] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/answers"
        params = {}
        if category_id is not None:
            params["category_id"] = category_id
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_kb_answers succeeded" if status_ok else "list_kb_answers failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_kb_answers failed: " + str(e)
            )

    async def get_kb_answer(
        self,
        id: int
    ) -> ZammadResponse:
        """Get KB answer

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/answers/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_kb_answer succeeded" if status_ok else "get_kb_answer failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_kb_answer failed: " + str(e)
            )

    async def create_kb_answer(
        self,
        category_id: int,
        translations: Dict,
        promoted: Optional[bool] = None
    ) -> ZammadResponse:
        """Create KB answer

        Args:
            category_id: int (required)
            translations: Dict (required)
            promoted: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/answers"
        request_body: Dict = {}
        if category_id is not None:
            request_body["category_id"] = category_id
        if translations is not None:
            request_body["translations"] = translations
        if promoted is not None:
            request_body["promoted"] = promoted

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_kb_answer succeeded" if status_ok else "create_kb_answer failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_kb_answer failed: " + str(e)
            )

    async def update_kb_answer(
        self,
        id: int,
        category_id: Optional[int] = None,
        promoted: Optional[bool] = None,
        translations: Optional[Dict] = None
    ) -> ZammadResponse:
        """Update KB answer

        Args:
            id: int (required)
            category_id: Optional[int] (optional)
            promoted: Optional[bool] (optional)
            translations: Optional[Dict] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/answers/{id}"
        request_body: Dict = {}
        if category_id is not None:
            request_body["category_id"] = category_id
        if promoted is not None:
            request_body["promoted"] = promoted
        if translations is not None:
            request_body["translations"] = translations

        try:
            request = HTTPRequest(
                url=url,
                method="PATCH",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_kb_answer succeeded" if status_ok else "update_kb_answer failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_kb_answer failed: " + str(e)
            )

    async def delete_kb_answer(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete KB answer

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/knowledge_bases/answers/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_kb_answer succeeded" if status_ok else "delete_kb_answer failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_kb_answer failed: " + str(e)
            )

    async def global_search(
        self,
        query: str,
        limit: Optional[int] = None,
        with_total_count: Optional[bool] = None,
        only_total_count: Optional[bool] = None
    ) -> ZammadResponse:
        """Global search across all objects

        Args:
            query: str (required)
            limit: Optional[int] (optional)
            with_total_count: Optional[bool] (optional)
            only_total_count: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/search"
        params = {}
        if query is not None:
            params["query"] = query
        if limit is not None:
            params["limit"] = limit
        if with_total_count is not None:
            params["with_total_count"] = with_total_count
        if only_total_count is not None:
            params["only_total_count"] = only_total_count
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="global_search succeeded" if status_ok else "global_search failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="global_search failed: " + str(e)
            )

    async def search_groups(
        self,
        query: str,
        limit: Optional[int] = None
    ) -> ZammadResponse:
        """Search groups

        Args:
            query: str (required)
            limit: Optional[int] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/groups/search"
        params = {}
        if query is not None:
            params["query"] = query
        if limit is not None:
            params["limit"] = limit
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="search_groups succeeded" if status_ok else "search_groups failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="search_groups failed: " + str(e)
            )

    async def search_roles(
        self,
        query: str,
        limit: Optional[int] = None
    ) -> ZammadResponse:
        """Search roles

        Args:
            query: str (required)
            limit: Optional[int] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/roles/search"
        params = {}
        if query is not None:
            params["query"] = query
        if limit is not None:
            params["limit"] = limit
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="search_roles succeeded" if status_ok else "search_roles failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="search_roles failed: " + str(e)
            )

    async def bulk_update_tickets(
        self,
        ticket_ids: List[int],
        attributes: Dict
    ) -> ZammadResponse:
        """Bulk update tickets

        Args:
            ticket_ids: List[int] (required)
            attributes: Dict (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/bulk"
        request_body: Dict = {}
        if ticket_ids is not None:
            request_body["ticket_ids"] = ticket_ids
        if attributes is not None:
            request_body["attributes"] = attributes

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="bulk_update_tickets succeeded" if status_ok else "bulk_update_tickets failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="bulk_update_tickets failed: " + str(e)
            )

    async def list_sessions(
        self
    ) -> ZammadResponse:
        """List active sessions

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/sessions"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_sessions succeeded" if status_ok else "list_sessions failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_sessions failed: " + str(e)
            )

    async def delete_session(
        self,
        id: str
    ) -> ZammadResponse:
        """Delete session

        Args:
            id: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/sessions/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_session succeeded" if status_ok else "delete_session failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_session failed: " + str(e)
            )

    async def list_user_devices(
        self
    ) -> ZammadResponse:
        """List user devices

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/user_devices"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_user_devices succeeded" if status_ok else "list_user_devices failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_user_devices failed: " + str(e)
            )

    async def delete_user_device(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete user device

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/user_devices/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_user_device succeeded" if status_ok else "delete_user_device failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_user_device failed: " + str(e)
            )

    async def password_reset_send(
        self,
        username: str
    ) -> ZammadResponse:
        """Send password reset email

        Args:
            username: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users/password_reset"
        request_body: Dict = {}
        if username is not None:
            request_body["username"] = username

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="password_reset_send succeeded" if status_ok else "password_reset_send failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="password_reset_send failed: " + str(e)
            )

    async def password_reset_verify(
        self,
        token: str,
        password: str
    ) -> ZammadResponse:
        """Verify password reset token and set new password

        Args:
            token: str (required)
            password: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users/password_reset_verify"
        request_body: Dict = {}
        if token is not None:
            request_body["token"] = token
        if password is not None:
            request_body["password"] = password

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="password_reset_verify succeeded" if status_ok else "password_reset_verify failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="password_reset_verify failed: " + str(e)
            )

    async def password_change(
        self,
        password_old: str,
        password_new: str
    ) -> ZammadResponse:
        """Change current user password

        Args:
            password_old: str (required)
            password_new: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users/password_change"
        request_body: Dict = {}
        if password_old is not None:
            request_body["password_old"] = password_old
        if password_new is not None:
            request_body["password_new"] = password_new

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="password_change succeeded" if status_ok else "password_change failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="password_change failed: " + str(e)
            )

    async def list_recent_views(
        self
    ) -> ZammadResponse:
        """List recent views

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/recent_view"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_recent_views succeeded" if status_ok else "list_recent_views failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_recent_views failed: " + str(e)
            )

    async def create_recent_view(
        self,
        object_type: str,
        o_id: int
    ) -> ZammadResponse:
        """Create recent view entry

        Args:
            object_type: str (required)
            o_id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/recent_view"
        request_body: Dict = {}
        if object_type is not None:
            request_body["object"] = object_type
        if o_id is not None:
            request_body["o_id"] = o_id

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_recent_view succeeded" if status_ok else "create_recent_view failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_recent_view failed: " + str(e)
            )

    async def import_users(
        self,
        data: str,
        try_import: Optional[bool] = None
    ) -> ZammadResponse:
        """Import users from CSV

        Args:
            data: str (required)
            try_import: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users/import"
        request_body: Dict = {}
        if data is not None:
            request_body["data"] = data
        if try_import is not None:
            request_body["try"] = try_import

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="import_users succeeded" if status_ok else "import_users failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="import_users failed: " + str(e)
            )

    async def import_organizations(
        self,
        data: str,
        try_import: Optional[bool] = None
    ) -> ZammadResponse:
        """Import organizations from CSV

        Args:
            data: str (required)
            try_import: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/organizations/import"
        request_body: Dict = {}
        if data is not None:
            request_body["data"] = data
        if try_import is not None:
            request_body["try"] = try_import

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="import_organizations succeeded" if status_ok else "import_organizations failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="import_organizations failed: " + str(e)
            )

    async def list_tickets(
        self,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
        expand: Optional[bool] = None
    ) -> ZammadResponse:
        """List tickets

        Args:
            page: Optional[int] (optional)
            per_page: Optional[int] (optional)
            expand: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets"
        params = {}
        if page is not None:
            params["page"] = page
        if per_page is not None:
            params["per_page"] = per_page
        if expand is not None:
            params["expand"] = expand
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_tickets succeeded" if status_ok else "list_tickets failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_tickets failed: " + str(e)
            )

    async def get_ticket(
        self,
        id: int,
        expand: Optional[bool] = None
    ) -> ZammadResponse:
        """Get ticket by ID

        Args:
            id: int (required)
            expand: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/{id}"
        params = {}
        if expand is not None:
            params["expand"] = expand
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_ticket succeeded" if status_ok else "get_ticket failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_ticket failed: " + str(e)
            )

    async def create_ticket(
        self,
        title: str,
        group: str,
        customer: Optional[str] = None,
        customer_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        state: Optional[str] = None,
        state_id: Optional[int] = None,
        priority: Optional[str] = None,
        priority_id: Optional[int] = None,
        owner: Optional[str] = None,
        owner_id: Optional[int] = None,
        article: Optional[Dict] = None,
        note: Optional[str] = None,
        mentions: Optional[List[int]] = None,
        pending_time: Optional[str] = None,
        type: Optional[str] = None,
        time_unit: Optional[float] = None
    ) -> ZammadResponse:
        """Create ticket

        Args:
            title: str (required)
            group: str (required)
            customer: Optional[str] (optional)
            customer_id: Optional[int] (optional)
            organization_id: Optional[int] (optional)
            state: Optional[str] (optional)
            state_id: Optional[int] (optional)
            priority: Optional[str] (optional)
            priority_id: Optional[int] (optional)
            owner: Optional[str] (optional)
            owner_id: Optional[int] (optional)
            article: Optional[Dict] (optional)
            note: Optional[str] (optional)
            mentions: Optional[List[int]] (optional)
            pending_time: Optional[str] (optional)
            type: Optional[str] (optional)
            time_unit: Optional[float] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets"
        request_body: Dict = {}
        if title is not None:
            request_body["title"] = title
        if group is not None:
            request_body["group"] = group
        if customer is not None:
            request_body["customer"] = customer
        if customer_id is not None:
            request_body["customer_id"] = customer_id
        if organization_id is not None:
            request_body["organization_id"] = organization_id
        if state is not None:
            request_body["state"] = state
        if state_id is not None:
            request_body["state_id"] = state_id
        if priority is not None:
            request_body["priority"] = priority
        if priority_id is not None:
            request_body["priority_id"] = priority_id
        if owner is not None:
            request_body["owner"] = owner
        if owner_id is not None:
            request_body["owner_id"] = owner_id
        if article is not None:
            request_body["article"] = article
        if note is not None:
            request_body["note"] = note
        if mentions is not None:
            request_body["mentions"] = mentions
        if pending_time is not None:
            request_body["pending_time"] = pending_time
        if type is not None:
            request_body["type"] = type
        if time_unit is not None:
            request_body["time_unit"] = time_unit

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_ticket succeeded" if status_ok else "create_ticket failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_ticket failed: " + str(e)
            )

    async def update_ticket(
        self,
        id: int,
        title: Optional[str] = None,
        group: Optional[str] = None,
        group_id: Optional[int] = None,
        state: Optional[str] = None,
        state_id: Optional[int] = None,
        priority: Optional[str] = None,
        priority_id: Optional[int] = None,
        owner: Optional[str] = None,
        owner_id: Optional[int] = None,
        customer_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        article: Optional[Dict] = None,
        note: Optional[str] = None,
        pending_time: Optional[str] = None,
        time_unit: Optional[float] = None
    ) -> ZammadResponse:
        """Update ticket

        Args:
            id: int (required)
            title: Optional[str] (optional)
            group: Optional[str] (optional)
            group_id: Optional[int] (optional)
            state: Optional[str] (optional)
            state_id: Optional[int] (optional)
            priority: Optional[str] (optional)
            priority_id: Optional[int] (optional)
            owner: Optional[str] (optional)
            owner_id: Optional[int] (optional)
            customer_id: Optional[int] (optional)
            organization_id: Optional[int] (optional)
            article: Optional[Dict] (optional)
            note: Optional[str] (optional)
            pending_time: Optional[str] (optional)
            time_unit: Optional[float] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/{id}"
        request_body: Dict = {}
        if title is not None:
            request_body["title"] = title
        if group is not None:
            request_body["group"] = group
        if group_id is not None:
            request_body["group_id"] = group_id
        if state is not None:
            request_body["state"] = state
        if state_id is not None:
            request_body["state_id"] = state_id
        if priority is not None:
            request_body["priority"] = priority
        if priority_id is not None:
            request_body["priority_id"] = priority_id
        if owner is not None:
            request_body["owner"] = owner
        if owner_id is not None:
            request_body["owner_id"] = owner_id
        if customer_id is not None:
            request_body["customer_id"] = customer_id
        if organization_id is not None:
            request_body["organization_id"] = organization_id
        if article is not None:
            request_body["article"] = article
        if note is not None:
            request_body["note"] = note
        if pending_time is not None:
            request_body["pending_time"] = pending_time
        if time_unit is not None:
            request_body["time_unit"] = time_unit

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_ticket succeeded" if status_ok else "update_ticket failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_ticket failed: " + str(e)
            )

    async def delete_ticket(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete ticket

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_ticket succeeded" if status_ok else "delete_ticket failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_ticket failed: " + str(e)
            )

    async def search_tickets(
        self,
        query: str,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
        expand: Optional[bool] = None,
        with_total_count: Optional[bool] = None,
        only_total_count: Optional[bool] = None
    ) -> ZammadResponse:
        """Search tickets

        Args:
            query: str (required)
            limit: Optional[int] (optional)
            page: Optional[int] (optional)
            per_page: Optional[int] (optional)
            expand: Optional[bool] (optional)
            with_total_count: Optional[bool] (optional)
            only_total_count: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/search"
        params = {}
        if query is not None:
            params["query"] = query
        if limit is not None:
            params["limit"] = limit
        if page is not None:
            params["page"] = page
        if per_page is not None:
            params["per_page"] = per_page
        if expand is not None:
            params["expand"] = expand
        if with_total_count is not None:
            params["with_total_count"] = with_total_count
        if only_total_count is not None:
            params["only_total_count"] = only_total_count
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="search_tickets succeeded" if status_ok else "search_tickets failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="search_tickets failed: " + str(e)
            )

    async def get_ticket_history(
        self,
        ticket_id: int
    ) -> ZammadResponse:
        """Get ticket history

        Args:
            ticket_id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_history/{ticket_id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_ticket_history succeeded" if status_ok else "get_ticket_history failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_ticket_history failed: " + str(e)
            )

    async def merge_tickets(
        self,
        source_id: int,
        target_id: int
    ) -> ZammadResponse:
        """Merge two tickets

        Args:
            source_id: int (required)
            target_id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_merge/{source_id}/{target_id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="merge_tickets succeeded" if status_ok else "merge_tickets failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="merge_tickets failed: " + str(e)
            )

    async def split_ticket(
        self,
        ticket_id: int,
        article_id: int,
        form_id: str
    ) -> ZammadResponse:
        """Split ticket article into new ticket

        Args:
            ticket_id: int (required)
            article_id: int (required)
            form_id: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_split"
        request_body: Dict = {}
        if ticket_id is not None:
            request_body["ticket_id"] = ticket_id
        if article_id is not None:
            request_body["article_id"] = article_id
        if form_id is not None:
            request_body["form_id"] = form_id

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="split_ticket succeeded" if status_ok else "split_ticket failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="split_ticket failed: " + str(e)
            )

    async def list_ticket_articles(
        self,
        ticket_id: int
    ) -> ZammadResponse:
        """List ticket articles

        Args:
            ticket_id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_articles/by_ticket/{ticket_id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_ticket_articles succeeded" if status_ok else "list_ticket_articles failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_ticket_articles failed: " + str(e)
            )

    async def get_ticket_article(
        self,
        id: int
    ) -> ZammadResponse:
        """Get ticket article

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_articles/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_ticket_article succeeded" if status_ok else "get_ticket_article failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_ticket_article failed: " + str(e)
            )

    async def create_ticket_article(
        self,
        ticket_id: int,
        body: str,
        subject: Optional[str] = None,
        type: Optional[str] = None,
        internal: Optional[bool] = None,
        time_unit: Optional[float] = None,
        from_field: Optional[str] = None,
        to: Optional[str] = None,
        cc: Optional[str] = None
    ) -> ZammadResponse:
        """Create ticket article

        Args:
            ticket_id: int (required)
            subject: Optional[str] (optional)
            body: str (required)
            type: Optional[str] (optional)
            internal: Optional[bool] (optional)
            time_unit: Optional[float] (optional)
            from_field: Optional[str] (optional)
            to: Optional[str] (optional)
            cc: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_articles"
        request_body: Dict = {}
        if ticket_id is not None:
            request_body["ticket_id"] = ticket_id
        if subject is not None:
            request_body["subject"] = subject
        if body is not None:
            request_body["body"] = body
        if type is not None:
            request_body["type"] = type
        if internal is not None:
            request_body["internal"] = internal
        if time_unit is not None:
            request_body["time_unit"] = time_unit
        if from_field is not None:
            request_body["from"] = from_field
        if to is not None:
            request_body["to"] = to
        if cc is not None:
            request_body["cc"] = cc

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_ticket_article succeeded" if status_ok else "create_ticket_article failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_ticket_article failed: " + str(e)
            )

    async def list_users(
        self,
        page: Optional[int] = None,
        per_page: Optional[int] = None
    ) -> ZammadResponse:
        """List users

        Args:
            page: Optional[int] (optional)
            per_page: Optional[int] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users"
        params = {}
        if page is not None:
            params["page"] = page
        if per_page is not None:
            params["per_page"] = per_page
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_users succeeded" if status_ok else "list_users failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_users failed: " + str(e)
            )

    async def get_user(
        self,
        id: int
    ) -> ZammadResponse:
        """Get user by ID

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_user succeeded" if status_ok else "get_user failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_user failed: " + str(e)
            )

    async def create_user(
        self,
        firstname: str,
        lastname: str,
        email: str,
        login: Optional[str] = None,
        password: Optional[str] = None,
        organization: Optional[str] = None,
        organization_id: Optional[int] = None,
        roles: Optional[List[str]] = None,
        role_ids: Optional[List[int]] = None,
        group_ids: Optional[Dict] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Create user

        Args:
            firstname: str (required)
            lastname: str (required)
            email: str (required)
            login: Optional[str] (optional)
            password: Optional[str] (optional)
            organization: Optional[str] (optional)
            organization_id: Optional[int] (optional)
            roles: Optional[List[str]] (optional)
            role_ids: Optional[List[int]] (optional)
            group_ids: Optional[Dict] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users"
        request_body: Dict = {}
        if firstname is not None:
            request_body["firstname"] = firstname
        if lastname is not None:
            request_body["lastname"] = lastname
        if email is not None:
            request_body["email"] = email
        if login is not None:
            request_body["login"] = login
        if password is not None:
            request_body["password"] = password
        if organization is not None:
            request_body["organization"] = organization
        if organization_id is not None:
            request_body["organization_id"] = organization_id
        if roles is not None:
            request_body["roles"] = roles
        if role_ids is not None:
            request_body["role_ids"] = role_ids
        if group_ids is not None:
            request_body["group_ids"] = group_ids
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_user succeeded" if status_ok else "create_user failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_user failed: " + str(e)
            )

    async def update_user(
        self,
        id: int,
        firstname: Optional[str] = None,
        lastname: Optional[str] = None,
        email: Optional[str] = None,
        login: Optional[str] = None,
        password: Optional[str] = None,
        organization: Optional[str] = None,
        organization_id: Optional[int] = None,
        roles: Optional[List[str]] = None,
        role_ids: Optional[List[int]] = None,
        group_ids: Optional[Dict] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Update user

        Args:
            id: int (required)
            firstname: Optional[str] (optional)
            lastname: Optional[str] (optional)
            email: Optional[str] (optional)
            login: Optional[str] (optional)
            password: Optional[str] (optional)
            organization: Optional[str] (optional)
            organization_id: Optional[int] (optional)
            roles: Optional[List[str]] (optional)
            role_ids: Optional[List[int]] (optional)
            group_ids: Optional[Dict] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users/{id}"
        request_body: Dict = {}
        if firstname is not None:
            request_body["firstname"] = firstname
        if lastname is not None:
            request_body["lastname"] = lastname
        if email is not None:
            request_body["email"] = email
        if login is not None:
            request_body["login"] = login
        if password is not None:
            request_body["password"] = password
        if organization is not None:
            request_body["organization"] = organization
        if organization_id is not None:
            request_body["organization_id"] = organization_id
        if roles is not None:
            request_body["roles"] = roles
        if role_ids is not None:
            request_body["role_ids"] = role_ids
        if group_ids is not None:
            request_body["group_ids"] = group_ids
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_user succeeded" if status_ok else "update_user failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_user failed: " + str(e)
            )

    async def delete_user(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete user

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_user succeeded" if status_ok else "delete_user failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_user failed: " + str(e)
            )

    async def search_users(
        self,
        query: str,
        limit: Optional[int] = None
    ) -> ZammadResponse:
        """Search users

        Args:
            query: str (required)
            limit: Optional[int] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users/search"
        params = {}
        if query is not None:
            params["query"] = query
        if limit is not None:
            params["limit"] = limit
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="search_users succeeded" if status_ok else "search_users failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="search_users failed: " + str(e)
            )

    async def get_current_user(
        self
    ) -> ZammadResponse:
        """Get current authenticated user

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users/me"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_current_user succeeded" if status_ok else "get_current_user failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_current_user failed: " + str(e)
            )

    async def list_groups(
        self
    ) -> ZammadResponse:
        """List groups

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/groups"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_groups succeeded" if status_ok else "list_groups failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_groups failed: " + str(e)
            )

    async def get_group(
        self,
        id: int
    ) -> ZammadResponse:
        """Get group by ID

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/groups/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_group succeeded" if status_ok else "get_group failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_group failed: " + str(e)
            )

    async def create_group(
        self,
        name: str,
        assignment_timeout: Optional[int] = None,
        follow_up_possible: Optional[str] = None,
        follow_up_assignment: Optional[bool] = None,
        email_address_id: Optional[int] = None,
        signature_id: Optional[int] = None,
        note: Optional[str] = None,
        active: Optional[bool] = None
    ) -> ZammadResponse:
        """Create group

        Args:
            name: str (required)
            assignment_timeout: Optional[int] (optional)
            follow_up_possible: Optional[str] (optional)
            follow_up_assignment: Optional[bool] (optional)
            email_address_id: Optional[int] (optional)
            signature_id: Optional[int] (optional)
            note: Optional[str] (optional)
            active: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/groups"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if assignment_timeout is not None:
            request_body["assignment_timeout"] = assignment_timeout
        if follow_up_possible is not None:
            request_body["follow_up_possible"] = follow_up_possible
        if follow_up_assignment is not None:
            request_body["follow_up_assignment"] = follow_up_assignment
        if email_address_id is not None:
            request_body["email_address_id"] = email_address_id
        if signature_id is not None:
            request_body["signature_id"] = signature_id
        if note is not None:
            request_body["note"] = note
        if active is not None:
            request_body["active"] = active

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_group succeeded" if status_ok else "create_group failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_group failed: " + str(e)
            )

    async def update_group(
        self,
        id: int,
        name: Optional[str] = None,
        assignment_timeout: Optional[int] = None,
        follow_up_possible: Optional[str] = None,
        follow_up_assignment: Optional[bool] = None,
        email_address_id: Optional[int] = None,
        signature_id: Optional[int] = None,
        note: Optional[str] = None,
        active: Optional[bool] = None
    ) -> ZammadResponse:
        """Update group

        Args:
            id: int (required)
            name: Optional[str] (optional)
            assignment_timeout: Optional[int] (optional)
            follow_up_possible: Optional[str] (optional)
            follow_up_assignment: Optional[bool] (optional)
            email_address_id: Optional[int] (optional)
            signature_id: Optional[int] (optional)
            note: Optional[str] (optional)
            active: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/groups/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if assignment_timeout is not None:
            request_body["assignment_timeout"] = assignment_timeout
        if follow_up_possible is not None:
            request_body["follow_up_possible"] = follow_up_possible
        if follow_up_assignment is not None:
            request_body["follow_up_assignment"] = follow_up_assignment
        if email_address_id is not None:
            request_body["email_address_id"] = email_address_id
        if signature_id is not None:
            request_body["signature_id"] = signature_id
        if note is not None:
            request_body["note"] = note
        if active is not None:
            request_body["active"] = active

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_group succeeded" if status_ok else "update_group failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_group failed: " + str(e)
            )

    async def delete_group(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete group

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/groups/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_group succeeded" if status_ok else "delete_group failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_group failed: " + str(e)
            )

    async def list_organizations(
        self,
        page: Optional[int] = None,
        per_page: Optional[int] = None
    ) -> ZammadResponse:
        """List organizations

        Args:
            page: Optional[int] (optional)
            per_page: Optional[int] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/organizations"
        params = {}
        if page is not None:
            params["page"] = page
        if per_page is not None:
            params["per_page"] = per_page
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_organizations succeeded" if status_ok else "list_organizations failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_organizations failed: " + str(e)
            )

    async def get_organization(
        self,
        id: int
    ) -> ZammadResponse:
        """Get organization by ID

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/organizations/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_organization succeeded" if status_ok else "get_organization failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_organization failed: " + str(e)
            )

    async def create_organization(
        self,
        name: str,
        shared: Optional[bool] = None,
        domain: Optional[str] = None,
        domain_assignment: Optional[bool] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Create organization

        Args:
            name: str (required)
            shared: Optional[bool] (optional)
            domain: Optional[str] (optional)
            domain_assignment: Optional[bool] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/organizations"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if shared is not None:
            request_body["shared"] = shared
        if domain is not None:
            request_body["domain"] = domain
        if domain_assignment is not None:
            request_body["domain_assignment"] = domain_assignment
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_organization succeeded" if status_ok else "create_organization failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_organization failed: " + str(e)
            )

    async def update_organization(
        self,
        id: int,
        name: Optional[str] = None,
        shared: Optional[bool] = None,
        domain: Optional[str] = None,
        domain_assignment: Optional[bool] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Update organization

        Args:
            id: int (required)
            name: Optional[str] (optional)
            shared: Optional[bool] (optional)
            domain: Optional[str] (optional)
            domain_assignment: Optional[bool] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/organizations/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if shared is not None:
            request_body["shared"] = shared
        if domain is not None:
            request_body["domain"] = domain
        if domain_assignment is not None:
            request_body["domain_assignment"] = domain_assignment
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_organization succeeded" if status_ok else "update_organization failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_organization failed: " + str(e)
            )

    async def delete_organization(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete organization

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/organizations/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_organization succeeded" if status_ok else "delete_organization failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_organization failed: " + str(e)
            )

    async def search_organizations(
        self,
        query: str,
        limit: Optional[int] = None
    ) -> ZammadResponse:
        """Search organizations

        Args:
            query: str (required)
            limit: Optional[int] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/organizations/search"
        params = {}
        if query is not None:
            params["query"] = query
        if limit is not None:
            params["limit"] = limit
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="search_organizations succeeded" if status_ok else "search_organizations failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="search_organizations failed: " + str(e)
            )

    async def list_roles(
        self
    ) -> ZammadResponse:
        """List roles

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/roles"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_roles succeeded" if status_ok else "list_roles failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_roles failed: " + str(e)
            )

    async def get_role(
        self,
        id: int
    ) -> ZammadResponse:
        """Get role by ID

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/roles/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_role succeeded" if status_ok else "get_role failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_role failed: " + str(e)
            )

    async def create_role(
        self,
        name: str,
        permissions: Optional[Dict] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Create role

        Args:
            name: str (required)
            permissions: Optional[Dict] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/roles"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if permissions is not None:
            request_body["permissions"] = permissions
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_role succeeded" if status_ok else "create_role failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_role failed: " + str(e)
            )

    async def update_role(
        self,
        id: int,
        name: Optional[str] = None,
        permissions: Optional[Dict] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Update role

        Args:
            id: int (required)
            name: Optional[str] (optional)
            permissions: Optional[Dict] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/roles/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if permissions is not None:
            request_body["permissions"] = permissions
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_role succeeded" if status_ok else "update_role failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_role failed: " + str(e)
            )

    async def delete_role(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete role

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/roles/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_role succeeded" if status_ok else "delete_role failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_role failed: " + str(e)
            )

    async def list_tags(
        self
    ) -> ZammadResponse:
        """List all tags

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tags"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_tags succeeded" if status_ok else "list_tags failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_tags failed: " + str(e)
            )

    async def add_tag(
        self,
        object_type: str,
        o_id: int,
        item: str
    ) -> ZammadResponse:
        """Add tag to object

        Args:
            object_type: str (required)
            o_id: int (required)
            item: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tags/add"
        params = {}
        if object_type is not None:
            params["object_type"] = object_type
        if o_id is not None:
            params["o_id"] = o_id
        if item is not None:
            params["item"] = item
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="add_tag succeeded" if status_ok else "add_tag failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="add_tag failed: " + str(e)
            )

    async def remove_tag(
        self,
        object_type: str,
        o_id: int,
        item: str
    ) -> ZammadResponse:
        """Remove tag from object

        Args:
            object_type: str (required)
            o_id: int (required)
            item: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tags/remove"
        params = {}
        if object_type is not None:
            params["object_type"] = object_type
        if o_id is not None:
            params["o_id"] = o_id
        if item is not None:
            params["item"] = item
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="remove_tag succeeded" if status_ok else "remove_tag failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="remove_tag failed: " + str(e)
            )

    async def search_tags(
        self,
        term: str
    ) -> ZammadResponse:
        """Search tags

        Args:
            term: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tag_search"
        params = {}
        if term is not None:
            params["term"] = term
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="search_tags succeeded" if status_ok else "search_tags failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="search_tags failed: " + str(e)
            )

    async def list_object_tags(
        self,
        object_type: str,
        o_id: int
    ) -> ZammadResponse:
        """List tags for object

        Args:
            object_type: str (required)
            o_id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tag_list"
        params = {}
        if object_type is not None:
            params["object_type"] = object_type
        if o_id is not None:
            params["o_id"] = o_id
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_object_tags succeeded" if status_ok else "list_object_tags failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_object_tags failed: " + str(e)
            )

    async def list_text_modules(
        self
    ) -> ZammadResponse:
        """List text modules

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/text_modules"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_text_modules succeeded" if status_ok else "list_text_modules failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_text_modules failed: " + str(e)
            )

    async def get_text_module(
        self,
        id: int
    ) -> ZammadResponse:
        """Get text module

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/text_modules/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_text_module succeeded" if status_ok else "get_text_module failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_text_module failed: " + str(e)
            )

    async def create_text_module(
        self,
        name: str,
        keywords: str,
        content: str,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Create text module

        Args:
            name: str (required)
            keywords: str (required)
            content: str (required)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/text_modules"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if keywords is not None:
            request_body["keywords"] = keywords
        if content is not None:
            request_body["content"] = content
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_text_module succeeded" if status_ok else "create_text_module failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_text_module failed: " + str(e)
            )

    async def update_text_module(
        self,
        id: int,
        name: Optional[str] = None,
        keywords: Optional[str] = None,
        content: Optional[str] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Update text module

        Args:
            id: int (required)
            name: Optional[str] (optional)
            keywords: Optional[str] (optional)
            content: Optional[str] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/text_modules/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if keywords is not None:
            request_body["keywords"] = keywords
        if content is not None:
            request_body["content"] = content
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_text_module succeeded" if status_ok else "update_text_module failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_text_module failed: " + str(e)
            )

    async def delete_text_module(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete text module

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/text_modules/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_text_module succeeded" if status_ok else "delete_text_module failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_text_module failed: " + str(e)
            )

    async def list_macros(
        self
    ) -> ZammadResponse:
        """List macros

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/macros"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_macros succeeded" if status_ok else "list_macros failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_macros failed: " + str(e)
            )

    async def get_macro(
        self,
        id: int
    ) -> ZammadResponse:
        """Get macro

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/macros/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_macro succeeded" if status_ok else "get_macro failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_macro failed: " + str(e)
            )

    async def create_macro(
        self,
        name: str,
        perform: Dict,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Create macro

        Args:
            name: str (required)
            perform: Dict (required)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/macros"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if perform is not None:
            request_body["perform"] = perform
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_macro succeeded" if status_ok else "create_macro failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_macro failed: " + str(e)
            )

    async def update_macro(
        self,
        id: int,
        name: Optional[str] = None,
        perform: Optional[Dict] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Update macro

        Args:
            id: int (required)
            name: Optional[str] (optional)
            perform: Optional[Dict] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/macros/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if perform is not None:
            request_body["perform"] = perform
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_macro succeeded" if status_ok else "update_macro failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_macro failed: " + str(e)
            )

    async def delete_macro(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete macro

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/macros/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_macro succeeded" if status_ok else "delete_macro failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_macro failed: " + str(e)
            )

    async def list_templates(
        self
    ) -> ZammadResponse:
        """List templates

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/templates"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_templates succeeded" if status_ok else "list_templates failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_templates failed: " + str(e)
            )

    async def get_template(
        self,
        id: int
    ) -> ZammadResponse:
        """Get template

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/templates/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_template succeeded" if status_ok else "get_template failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_template failed: " + str(e)
            )

    async def create_template(
        self,
        name: str,
        options: Dict,
        active: Optional[bool] = None
    ) -> ZammadResponse:
        """Create template

        Args:
            name: str (required)
            options: Dict (required)
            active: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/templates"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if options is not None:
            request_body["options"] = options
        if active is not None:
            request_body["active"] = active

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_template succeeded" if status_ok else "create_template failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_template failed: " + str(e)
            )

    async def update_template(
        self,
        id: int,
        name: Optional[str] = None,
        options: Optional[Dict] = None,
        active: Optional[bool] = None
    ) -> ZammadResponse:
        """Update template

        Args:
            id: int (required)
            name: Optional[str] (optional)
            options: Optional[Dict] (optional)
            active: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/templates/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if options is not None:
            request_body["options"] = options
        if active is not None:
            request_body["active"] = active

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_template succeeded" if status_ok else "update_template failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_template failed: " + str(e)
            )

    async def delete_template(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete template

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/templates/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_template succeeded" if status_ok else "delete_template failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_template failed: " + str(e)
            )

    async def list_signatures(
        self
    ) -> ZammadResponse:
        """List signatures

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/signatures"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_signatures succeeded" if status_ok else "list_signatures failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_signatures failed: " + str(e)
            )

    async def get_signature(
        self,
        id: int
    ) -> ZammadResponse:
        """Get signature

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/signatures/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_signature succeeded" if status_ok else "get_signature failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_signature failed: " + str(e)
            )

    async def create_signature(
        self,
        name: str,
        body: str,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Create signature

        Args:
            name: str (required)
            body: str (required)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/signatures"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if body is not None:
            request_body["body"] = body
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_signature succeeded" if status_ok else "create_signature failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_signature failed: " + str(e)
            )

    async def update_signature(
        self,
        id: int,
        name: Optional[str] = None,
        body: Optional[str] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Update signature

        Args:
            id: int (required)
            name: Optional[str] (optional)
            body: Optional[str] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/signatures/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if body is not None:
            request_body["body"] = body
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_signature succeeded" if status_ok else "update_signature failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_signature failed: " + str(e)
            )

    async def delete_signature(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete signature

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/signatures/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_signature succeeded" if status_ok else "delete_signature failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_signature failed: " + str(e)
            )

    async def list_email_addresses(
        self
    ) -> ZammadResponse:
        """List email addresses

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/email_addresses"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_email_addresses succeeded" if status_ok else "list_email_addresses failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_email_addresses failed: " + str(e)
            )

    async def get_email_address(
        self,
        id: int
    ) -> ZammadResponse:
        """Get email address

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/email_addresses/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_email_address succeeded" if status_ok else "get_email_address failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_email_address failed: " + str(e)
            )

    async def create_email_address(
        self,
        name: str,
        email: str,
        channel_id: int,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Create email address

        Args:
            name: str (required)
            email: str (required)
            channel_id: int (required)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/email_addresses"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if email is not None:
            request_body["email"] = email
        if channel_id is not None:
            request_body["channel_id"] = channel_id
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_email_address succeeded" if status_ok else "create_email_address failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_email_address failed: " + str(e)
            )

    async def update_email_address(
        self,
        id: int,
        name: Optional[str] = None,
        email: Optional[str] = None,
        channel_id: Optional[int] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Update email address

        Args:
            id: int (required)
            name: Optional[str] (optional)
            email: Optional[str] (optional)
            channel_id: Optional[int] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/email_addresses/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if email is not None:
            request_body["email"] = email
        if channel_id is not None:
            request_body["channel_id"] = channel_id
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_email_address succeeded" if status_ok else "update_email_address failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_email_address failed: " + str(e)
            )

    async def delete_email_address(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete email address

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/email_addresses/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_email_address succeeded" if status_ok else "delete_email_address failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_email_address failed: " + str(e)
            )

    async def list_overviews(
        self
    ) -> ZammadResponse:
        """List overviews

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/overviews"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_overviews succeeded" if status_ok else "list_overviews failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_overviews failed: " + str(e)
            )

    async def get_overview(
        self,
        id: int
    ) -> ZammadResponse:
        """Get overview

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/overviews/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_overview succeeded" if status_ok else "get_overview failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_overview failed: " + str(e)
            )

    async def create_overview(
        self,
        name: str,
        link: str,
        prio: int,
        condition: Dict,
        order: Dict,
        view: Dict,
        active: Optional[bool] = None
    ) -> ZammadResponse:
        """Create overview

        Args:
            name: str (required)
            link: str (required)
            prio: int (required)
            condition: Dict (required)
            order: Dict (required)
            view: Dict (required)
            active: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/overviews"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if link is not None:
            request_body["link"] = link
        if prio is not None:
            request_body["prio"] = prio
        if condition is not None:
            request_body["condition"] = condition
        if order is not None:
            request_body["order"] = order
        if view is not None:
            request_body["view"] = view
        if active is not None:
            request_body["active"] = active

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_overview succeeded" if status_ok else "create_overview failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_overview failed: " + str(e)
            )

    async def update_overview(
        self,
        id: int,
        name: Optional[str] = None,
        link: Optional[str] = None,
        prio: Optional[int] = None,
        condition: Optional[Dict] = None,
        order: Optional[Dict] = None,
        view: Optional[Dict] = None,
        active: Optional[bool] = None
    ) -> ZammadResponse:
        """Update overview

        Args:
            id: int (required)
            name: Optional[str] (optional)
            link: Optional[str] (optional)
            prio: Optional[int] (optional)
            condition: Optional[Dict] (optional)
            order: Optional[Dict] (optional)
            view: Optional[Dict] (optional)
            active: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/overviews/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if link is not None:
            request_body["link"] = link
        if prio is not None:
            request_body["prio"] = prio
        if condition is not None:
            request_body["condition"] = condition
        if order is not None:
            request_body["order"] = order
        if view is not None:
            request_body["view"] = view
        if active is not None:
            request_body["active"] = active

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_overview succeeded" if status_ok else "update_overview failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_overview failed: " + str(e)
            )

    async def delete_overview(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete overview

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/overviews/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_overview succeeded" if status_ok else "delete_overview failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_overview failed: " + str(e)
            )

    async def list_triggers(
        self
    ) -> ZammadResponse:
        """List triggers

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/triggers"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_triggers succeeded" if status_ok else "list_triggers failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_triggers failed: " + str(e)
            )

    async def get_trigger(
        self,
        id: int
    ) -> ZammadResponse:
        """Get trigger

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/triggers/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_trigger succeeded" if status_ok else "get_trigger failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_trigger failed: " + str(e)
            )

    async def create_trigger(
        self,
        name: str,
        condition: Dict,
        perform: Dict,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Create trigger

        Args:
            name: str (required)
            condition: Dict (required)
            perform: Dict (required)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/triggers"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if condition is not None:
            request_body["condition"] = condition
        if perform is not None:
            request_body["perform"] = perform
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_trigger succeeded" if status_ok else "create_trigger failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_trigger failed: " + str(e)
            )

    async def update_trigger(
        self,
        id: int,
        name: Optional[str] = None,
        condition: Optional[Dict] = None,
        perform: Optional[Dict] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Update trigger

        Args:
            id: int (required)
            name: Optional[str] (optional)
            condition: Optional[Dict] (optional)
            perform: Optional[Dict] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/triggers/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if condition is not None:
            request_body["condition"] = condition
        if perform is not None:
            request_body["perform"] = perform
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_trigger succeeded" if status_ok else "update_trigger failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_trigger failed: " + str(e)
            )

    async def delete_trigger(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete trigger

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/triggers/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_trigger succeeded" if status_ok else "delete_trigger failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_trigger failed: " + str(e)
            )

    async def list_jobs(
        self
    ) -> ZammadResponse:
        """List jobs

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/jobs"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_jobs succeeded" if status_ok else "list_jobs failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_jobs failed: " + str(e)
            )

    async def get_job(
        self,
        id: int
    ) -> ZammadResponse:
        """Get job

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/jobs/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_job succeeded" if status_ok else "get_job failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_job failed: " + str(e)
            )

    async def create_job(
        self,
        name: str,
        timeplan: Dict,
        condition: Dict,
        perform: Dict,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Create job

        Args:
            name: str (required)
            timeplan: Dict (required)
            condition: Dict (required)
            perform: Dict (required)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/jobs"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if timeplan is not None:
            request_body["timeplan"] = timeplan
        if condition is not None:
            request_body["condition"] = condition
        if perform is not None:
            request_body["perform"] = perform
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_job succeeded" if status_ok else "create_job failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_job failed: " + str(e)
            )

    async def update_job(
        self,
        id: int,
        name: Optional[str] = None,
        timeplan: Optional[Dict] = None,
        condition: Optional[Dict] = None,
        perform: Optional[Dict] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Update job

        Args:
            id: int (required)
            name: Optional[str] (optional)
            timeplan: Optional[Dict] (optional)
            condition: Optional[Dict] (optional)
            perform: Optional[Dict] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/jobs/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if timeplan is not None:
            request_body["timeplan"] = timeplan
        if condition is not None:
            request_body["condition"] = condition
        if perform is not None:
            request_body["perform"] = perform
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_job succeeded" if status_ok else "update_job failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_job failed: " + str(e)
            )

    async def delete_job(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete job

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/jobs/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_job succeeded" if status_ok else "delete_job failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_job failed: " + str(e)
            )

    async def list_slas(
        self
    ) -> ZammadResponse:
        """List SLAs

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/slas"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_slas succeeded" if status_ok else "list_slas failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_slas failed: " + str(e)
            )

    async def get_sla(
        self,
        id: int
    ) -> ZammadResponse:
        """Get SLA

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/slas/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_sla succeeded" if status_ok else "get_sla failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_sla failed: " + str(e)
            )

    async def create_sla(
        self,
        name: str,
        calendar_id: int,
        first_response_time: Optional[int] = None,
        update_time: Optional[int] = None,
        solution_time: Optional[int] = None,
        condition: Optional[Dict] = None,
        active: Optional[bool] = None
    ) -> ZammadResponse:
        """Create SLA

        Args:
            name: str (required)
            calendar_id: int (required)
            first_response_time: Optional[int] (optional)
            update_time: Optional[int] (optional)
            solution_time: Optional[int] (optional)
            condition: Optional[Dict] (optional)
            active: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/slas"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if calendar_id is not None:
            request_body["calendar_id"] = calendar_id
        if first_response_time is not None:
            request_body["first_response_time"] = first_response_time
        if update_time is not None:
            request_body["update_time"] = update_time
        if solution_time is not None:
            request_body["solution_time"] = solution_time
        if condition is not None:
            request_body["condition"] = condition
        if active is not None:
            request_body["active"] = active

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_sla succeeded" if status_ok else "create_sla failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_sla failed: " + str(e)
            )

    async def update_sla(
        self,
        id: int,
        name: Optional[str] = None,
        calendar_id: Optional[int] = None,
        first_response_time: Optional[int] = None,
        update_time: Optional[int] = None,
        solution_time: Optional[int] = None,
        condition: Optional[Dict] = None,
        active: Optional[bool] = None
    ) -> ZammadResponse:
        """Update SLA

        Args:
            id: int (required)
            name: Optional[str] (optional)
            calendar_id: Optional[int] (optional)
            first_response_time: Optional[int] (optional)
            update_time: Optional[int] (optional)
            solution_time: Optional[int] (optional)
            condition: Optional[Dict] (optional)
            active: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/slas/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if calendar_id is not None:
            request_body["calendar_id"] = calendar_id
        if first_response_time is not None:
            request_body["first_response_time"] = first_response_time
        if update_time is not None:
            request_body["update_time"] = update_time
        if solution_time is not None:
            request_body["solution_time"] = solution_time
        if condition is not None:
            request_body["condition"] = condition
        if active is not None:
            request_body["active"] = active

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_sla succeeded" if status_ok else "update_sla failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_sla failed: " + str(e)
            )

    async def delete_sla(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete SLA

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/slas/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_sla succeeded" if status_ok else "delete_sla failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_sla failed: " + str(e)
            )

    async def list_calendars(
        self
    ) -> ZammadResponse:
        """List calendars

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/calendars"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_calendars succeeded" if status_ok else "list_calendars failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_calendars failed: " + str(e)
            )

    async def get_calendar(
        self,
        id: int
    ) -> ZammadResponse:
        """Get calendar

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/calendars/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_calendar succeeded" if status_ok else "get_calendar failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_calendar failed: " + str(e)
            )

    async def create_calendar(
        self,
        name: str,
        timezone: str,
        business_hours: Dict,
        public_holidays: Optional[Dict] = None,
        ical_url: Optional[str] = None
    ) -> ZammadResponse:
        """Create calendar

        Args:
            name: str (required)
            timezone: str (required)
            business_hours: Dict (required)
            public_holidays: Optional[Dict] (optional)
            ical_url: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/calendars"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if timezone is not None:
            request_body["timezone"] = timezone
        if business_hours is not None:
            request_body["business_hours"] = business_hours
        if public_holidays is not None:
            request_body["public_holidays"] = public_holidays
        if ical_url is not None:
            request_body["ical_url"] = ical_url

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_calendar succeeded" if status_ok else "create_calendar failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_calendar failed: " + str(e)
            )

    async def update_calendar(
        self,
        id: int,
        name: Optional[str] = None,
        timezone: Optional[str] = None,
        business_hours: Optional[Dict] = None,
        public_holidays: Optional[Dict] = None,
        ical_url: Optional[str] = None
    ) -> ZammadResponse:
        """Update calendar

        Args:
            id: int (required)
            name: Optional[str] (optional)
            timezone: Optional[str] (optional)
            business_hours: Optional[Dict] (optional)
            public_holidays: Optional[Dict] (optional)
            ical_url: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/calendars/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if timezone is not None:
            request_body["timezone"] = timezone
        if business_hours is not None:
            request_body["business_hours"] = business_hours
        if public_holidays is not None:
            request_body["public_holidays"] = public_holidays
        if ical_url is not None:
            request_body["ical_url"] = ical_url

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_calendar succeeded" if status_ok else "update_calendar failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_calendar failed: " + str(e)
            )

    async def delete_calendar(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete calendar

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/calendars/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_calendar succeeded" if status_ok else "delete_calendar failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_calendar failed: " + str(e)
            )

    async def list_ticket_states(
        self
    ) -> ZammadResponse:
        """List ticket states

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_states"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_ticket_states succeeded" if status_ok else "list_ticket_states failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_ticket_states failed: " + str(e)
            )

    async def get_ticket_state(
        self,
        id: int
    ) -> ZammadResponse:
        """Get ticket state

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_states/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_ticket_state succeeded" if status_ok else "get_ticket_state failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_ticket_state failed: " + str(e)
            )

    async def create_ticket_state(
        self,
        name: str,
        state_type_id: int,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Create ticket state

        Args:
            name: str (required)
            state_type_id: int (required)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_states"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if state_type_id is not None:
            request_body["state_type_id"] = state_type_id
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_ticket_state succeeded" if status_ok else "create_ticket_state failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_ticket_state failed: " + str(e)
            )

    async def update_ticket_state(
        self,
        id: int,
        name: Optional[str] = None,
        state_type_id: Optional[int] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Update ticket state

        Args:
            id: int (required)
            name: Optional[str] (optional)
            state_type_id: Optional[int] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_states/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if state_type_id is not None:
            request_body["state_type_id"] = state_type_id
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_ticket_state succeeded" if status_ok else "update_ticket_state failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_ticket_state failed: " + str(e)
            )

    async def delete_ticket_state(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete ticket state

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_states/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_ticket_state succeeded" if status_ok else "delete_ticket_state failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_ticket_state failed: " + str(e)
            )

    async def list_ticket_priorities(
        self
    ) -> ZammadResponse:
        """List ticket priorities

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_priorities"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_ticket_priorities succeeded" if status_ok else "list_ticket_priorities failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_ticket_priorities failed: " + str(e)
            )

    async def get_ticket_priority(
        self,
        id: int
    ) -> ZammadResponse:
        """Get ticket priority

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_priorities/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_ticket_priority succeeded" if status_ok else "get_ticket_priority failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_ticket_priority failed: " + str(e)
            )

    async def create_ticket_priority(
        self,
        name: str,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Create ticket priority

        Args:
            name: str (required)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_priorities"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_ticket_priority succeeded" if status_ok else "create_ticket_priority failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_ticket_priority failed: " + str(e)
            )

    async def update_ticket_priority(
        self,
        id: int,
        name: Optional[str] = None,
        active: Optional[bool] = None,
        note: Optional[str] = None
    ) -> ZammadResponse:
        """Update ticket priority

        Args:
            id: int (required)
            name: Optional[str] (optional)
            active: Optional[bool] (optional)
            note: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_priorities/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if active is not None:
            request_body["active"] = active
        if note is not None:
            request_body["note"] = note

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_ticket_priority succeeded" if status_ok else "update_ticket_priority failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_ticket_priority failed: " + str(e)
            )

    async def delete_ticket_priority(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete ticket priority

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/ticket_priorities/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_ticket_priority succeeded" if status_ok else "delete_ticket_priority failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_ticket_priority failed: " + str(e)
            )

    async def list_online_notifications(
        self,
        expand: Optional[bool] = None
    ) -> ZammadResponse:
        """List online notifications

        Args:
            expand: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/online_notifications"
        params = {}
        if expand is not None:
            params["expand"] = expand
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_online_notifications succeeded" if status_ok else "list_online_notifications failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_online_notifications failed: " + str(e)
            )

    async def mark_notification_read(
        self
    ) -> ZammadResponse:
        """Mark all notifications as read

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/online_notifications/mark_all_as_read"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="mark_notification_read succeeded" if status_ok else "mark_notification_read failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="mark_notification_read failed: " + str(e)
            )

    async def delete_notification(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete online notification

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/online_notifications/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_notification succeeded" if status_ok else "delete_notification failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_notification failed: " + str(e)
            )

    async def list_avatars(
        self,
        id: int
    ) -> ZammadResponse:
        """Get user avatar

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users/avatar/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_avatars succeeded" if status_ok else "list_avatars failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_avatars failed: " + str(e)
            )

    async def set_avatar(
        self,
        avatar_full: str
    ) -> ZammadResponse:
        """Set user avatar

        Args:
            avatar_full: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users/avatar"
        request_body: Dict = {}
        if avatar_full is not None:
            request_body["avatar_full"] = avatar_full

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="set_avatar succeeded" if status_ok else "set_avatar failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="set_avatar failed: " + str(e)
            )

    async def delete_avatar(
        self
    ) -> ZammadResponse:
        """Delete user avatar

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/users/avatar"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_avatar succeeded" if status_ok else "delete_avatar failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_avatar failed: " + str(e)
            )

    async def list_time_accountings(
        self,
        ticket_id: Optional[int] = None
    ) -> ZammadResponse:
        """List time accountings

        Args:
            ticket_id: Optional[int] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/time_accountings"
        params = {}
        if ticket_id is not None:
            params["ticket_id"] = ticket_id
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_time_accountings succeeded" if status_ok else "list_time_accountings failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_time_accountings failed: " + str(e)
            )

    async def get_time_accounting(
        self,
        id: int
    ) -> ZammadResponse:
        """Get time accounting

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/time_accountings/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_time_accounting succeeded" if status_ok else "get_time_accounting failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_time_accounting failed: " + str(e)
            )

    async def create_time_accounting(
        self,
        ticket_id: int,
        time_unit: float,
        ticket_article_id: Optional[int] = None
    ) -> ZammadResponse:
        """Create time accounting entry

        Args:
            ticket_id: int (required)
            ticket_article_id: Optional[int] (optional)
            time_unit: float (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/time_accountings"
        request_body: Dict = {}
        if ticket_id is not None:
            request_body["ticket_id"] = ticket_id
        if ticket_article_id is not None:
            request_body["ticket_article_id"] = ticket_article_id
        if time_unit is not None:
            request_body["time_unit"] = time_unit

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_time_accounting succeeded" if status_ok else "create_time_accounting failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_time_accounting failed: " + str(e)
            )

    async def update_time_accounting(
        self,
        id: int,
        time_unit: Optional[float] = None
    ) -> ZammadResponse:
        """Update time accounting

        Args:
            id: int (required)
            time_unit: Optional[float] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/time_accountings/{id}"
        request_body: Dict = {}
        if time_unit is not None:
            request_body["time_unit"] = time_unit

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_time_accounting succeeded" if status_ok else "update_time_accounting failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_time_accounting failed: " + str(e)
            )

    async def delete_time_accounting(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete time accounting

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/time_accountings/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_time_accounting succeeded" if status_ok else "delete_time_accounting failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_time_accounting failed: " + str(e)
            )

    async def add_link(
        self,
        link_type: str,
        link_object_source: str,
        link_object_source_value: int,
        link_object_target: str,
        link_object_target_value: int
    ) -> ZammadResponse:
        """Add link between objects

        Args:
            link_type: str (required)
            link_object_source: str (required)
            link_object_source_value: int (required)
            link_object_target: str (required)
            link_object_target_value: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/links/add"
        request_body: Dict = {}
        if link_type is not None:
            request_body["link_type"] = link_type
        if link_object_source is not None:
            request_body["link_object_source"] = link_object_source
        if link_object_source_value is not None:
            request_body["link_object_source_value"] = link_object_source_value
        if link_object_target is not None:
            request_body["link_object_target"] = link_object_target
        if link_object_target_value is not None:
            request_body["link_object_target_value"] = link_object_target_value

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="add_link succeeded" if status_ok else "add_link failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="add_link failed: " + str(e)
            )

    async def remove_link(
        self,
        link_type: str,
        link_object_source: str,
        link_object_source_value: int,
        link_object_target: str,
        link_object_target_value: int
    ) -> ZammadResponse:
        """Remove link between objects

        Args:
            link_type: str (required)
            link_object_source: str (required)
            link_object_source_value: int (required)
            link_object_target: str (required)
            link_object_target_value: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/links/remove"
        params = {}
        if link_type is not None:
            params["link_type"] = link_type
        if link_object_source is not None:
            params["link_object_source"] = link_object_source
        if link_object_source_value is not None:
            params["link_object_source_value"] = link_object_source_value
        if link_object_target is not None:
            params["link_object_target"] = link_object_target
        if link_object_target_value is not None:
            params["link_object_target_value"] = link_object_target_value
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="remove_link succeeded" if status_ok else "remove_link failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="remove_link failed: " + str(e)
            )

    async def list_links(
        self,
        link_object: str,
        link_object_value: int
    ) -> ZammadResponse:
        """List links for object

        Args:
            link_object: str (required)
            link_object_value: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/links"
        params = {}
        if link_object is not None:
            params["link_object"] = link_object
        if link_object_value is not None:
            params["link_object_value"] = link_object_value
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_links succeeded" if status_ok else "list_links failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_links failed: " + str(e)
            )

    async def list_mentions(
        self,
        mentionable_type: str,
        mentionable_id: int
    ) -> ZammadResponse:
        """List mentions

        Args:
            mentionable_type: str (required)
            mentionable_id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/mentions"
        params = {}
        if mentionable_type is not None:
            params["mentionable_type"] = mentionable_type
        if mentionable_id is not None:
            params["mentionable_id"] = mentionable_id
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_mentions succeeded" if status_ok else "list_mentions failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_mentions failed: " + str(e)
            )

    async def create_mention(
        self,
        mentionable_type: str,
        mentionable_id: int,
        user_id: int
    ) -> ZammadResponse:
        """Create mention

        Args:
            mentionable_type: str (required)
            mentionable_id: int (required)
            user_id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/mentions"
        request_body: Dict = {}
        if mentionable_type is not None:
            request_body["mentionable_type"] = mentionable_type
        if mentionable_id is not None:
            request_body["mentionable_id"] = mentionable_id
        if user_id is not None:
            request_body["user_id"] = user_id

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_mention succeeded" if status_ok else "create_mention failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_mention failed: " + str(e)
            )

    async def delete_mention(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete mention

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/mentions/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_mention succeeded" if status_ok else "delete_mention failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_mention failed: " + str(e)
            )

    async def list_object_attributes(
        self
    ) -> ZammadResponse:
        """List object attributes

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/object_manager_attributes"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_object_attributes succeeded" if status_ok else "list_object_attributes failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_object_attributes failed: " + str(e)
            )

    async def get_object_attribute(
        self,
        id: int
    ) -> ZammadResponse:
        """Get object attribute

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/object_manager_attributes/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_object_attribute succeeded" if status_ok else "get_object_attribute failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_object_attribute failed: " + str(e)
            )

    async def create_object_attribute(
        self,
        object_name: str,
        name: str,
        display: str,
        data_type: str,
        data_option: Dict,
        active: Optional[bool] = None,
        screens: Optional[Dict] = None,
        position: Optional[int] = None
    ) -> ZammadResponse:
        """Create object attribute

        Args:
            object_name: str (required)
            name: str (required)
            display: str (required)
            data_type: str (required)
            data_option: Dict (required)
            active: Optional[bool] (optional)
            screens: Optional[Dict] (optional)
            position: Optional[int] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/object_manager_attributes"
        request_body: Dict = {}
        if object_name is not None:
            request_body["object"] = object_name
        if name is not None:
            request_body["name"] = name
        if display is not None:
            request_body["display"] = display
        if data_type is not None:
            request_body["data_type"] = data_type
        if data_option is not None:
            request_body["data_option"] = data_option
        if active is not None:
            request_body["active"] = active
        if screens is not None:
            request_body["screens"] = screens
        if position is not None:
            request_body["position"] = position

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_object_attribute succeeded" if status_ok else "create_object_attribute failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_object_attribute failed: " + str(e)
            )

    async def update_object_attribute(
        self,
        id: int,
        display: Optional[str] = None,
        data_option: Optional[Dict] = None,
        screens: Optional[Dict] = None,
        active: Optional[bool] = None
    ) -> ZammadResponse:
        """Update object attribute

        Args:
            id: int (required)
            display: Optional[str] (optional)
            data_option: Optional[Dict] (optional)
            screens: Optional[Dict] (optional)
            active: Optional[bool] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/object_manager_attributes/{id}"
        request_body: Dict = {}
        if display is not None:
            request_body["display"] = display
        if data_option is not None:
            request_body["data_option"] = data_option
        if screens is not None:
            request_body["screens"] = screens
        if active is not None:
            request_body["active"] = active

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_object_attribute succeeded" if status_ok else "update_object_attribute failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_object_attribute failed: " + str(e)
            )

    async def delete_object_attribute(
        self,
        id: int
    ) -> ZammadResponse:
        """Delete object attribute

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/object_manager_attributes/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_object_attribute succeeded" if status_ok else "delete_object_attribute failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_object_attribute failed: " + str(e)
            )

    async def execute_object_migrations(
        self
    ) -> ZammadResponse:
        """Execute object manager migrations

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/object_manager_attributes_execute_migrations"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="execute_object_migrations succeeded" if status_ok else "execute_object_migrations failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="execute_object_migrations failed: " + str(e)
            )

    async def get_ticket_checklist(
        self,
        ticket_id: int
    ) -> ZammadResponse:
        """Get ticket checklist

        Args:
            ticket_id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/{ticket_id}/checklist"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_ticket_checklist succeeded" if status_ok else "get_ticket_checklist failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_ticket_checklist failed: " + str(e)
            )

    async def create_ticket_checklist(
        self,
        ticket_id: int,
        name: str,
        items: List[Dict]
    ) -> ZammadResponse:
        """Create ticket checklist

        Args:
            ticket_id: int (required)
            name: str (required)
            items: List[Dict] (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/{ticket_id}/checklist"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if items is not None:
            request_body["items"] = items

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_ticket_checklist succeeded" if status_ok else "create_ticket_checklist failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_ticket_checklist failed: " + str(e)
            )

    async def update_ticket_checklist(
        self,
        ticket_id: int,
        id: int,
        name: Optional[str] = None,
        items: Optional[List[Dict]] = None
    ) -> ZammadResponse:
        """Update ticket checklist

        Args:
            ticket_id: int (required)
            id: int (required)
            name: Optional[str] (optional)
            items: Optional[List[Dict]] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/{ticket_id}/checklist/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if items is not None:
            request_body["items"] = items

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_ticket_checklist succeeded" if status_ok else "update_ticket_checklist failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_ticket_checklist failed: " + str(e)
            )

    async def delete_ticket_checklist(
        self,
        ticket_id: int,
        id: int
    ) -> ZammadResponse:
        """Delete ticket checklist

        Args:
            ticket_id: int (required)
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/{ticket_id}/checklist/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_ticket_checklist succeeded" if status_ok else "delete_ticket_checklist failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_ticket_checklist failed: " + str(e)
            )

    async def list_shared_drafts(
        self,
        ticket_id: int
    ) -> ZammadResponse:
        """List shared drafts for ticket

        Args:
            ticket_id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/{ticket_id}/shared_drafts"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_shared_drafts succeeded" if status_ok else "list_shared_drafts failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_shared_drafts failed: " + str(e)
            )

    async def get_shared_draft(
        self,
        ticket_id: int,
        id: int
    ) -> ZammadResponse:
        """Get shared draft

        Args:
            ticket_id: int (required)
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/{ticket_id}/shared_drafts/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_shared_draft succeeded" if status_ok else "get_shared_draft failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_shared_draft failed: " + str(e)
            )

    async def create_shared_draft(
        self,
        ticket_id: int,
        name: str,
        content: Dict
    ) -> ZammadResponse:
        """Create shared draft

        Args:
            ticket_id: int (required)
            name: str (required)
            content: Dict (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/{ticket_id}/shared_drafts"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if content is not None:
            request_body["content"] = content

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="create_shared_draft succeeded" if status_ok else "create_shared_draft failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="create_shared_draft failed: " + str(e)
            )

    async def update_shared_draft(
        self,
        ticket_id: int,
        id: int,
        name: Optional[str] = None,
        content: Optional[Dict] = None
    ) -> ZammadResponse:
        """Update shared draft

        Args:
            ticket_id: int (required)
            id: int (required)
            name: Optional[str] (optional)
            content: Optional[Dict] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/{ticket_id}/shared_drafts/{id}"
        request_body: Dict = {}
        if name is not None:
            request_body["name"] = name
        if content is not None:
            request_body["content"] = content

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_shared_draft succeeded" if status_ok else "update_shared_draft failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_shared_draft failed: " + str(e)
            )

    async def delete_shared_draft(
        self,
        ticket_id: int,
        id: int
    ) -> ZammadResponse:
        """Delete shared draft

        Args:
            ticket_id: int (required)
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/{ticket_id}/shared_drafts/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="delete_shared_draft succeeded" if status_ok else "delete_shared_draft failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="delete_shared_draft failed: " + str(e)
            )

    async def list_channels(
        self
    ) -> ZammadResponse:
        """List channels

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/channels"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_channels succeeded" if status_ok else "list_channels failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_channels failed: " + str(e)
            )

    async def get_channel(
        self,
        id: int
    ) -> ZammadResponse:
        """Get channel

        Args:
            id: int (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/channels/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_channel succeeded" if status_ok else "get_channel failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_channel failed: " + str(e)
            )

    async def generate_report(
        self,
        metric: str,
        year: int,
        month: Optional[int] = None,
        week: Optional[int] = None,
        day: Optional[int] = None
    ) -> ZammadResponse:
        """Generate report

        Args:
            metric: str (required)
            year: int (required)
            month: Optional[int] (optional)
            week: Optional[int] (optional)
            day: Optional[int] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/reports/generate"
        request_body: Dict = {}
        if metric is not None:
            request_body["metric"] = metric
        if year is not None:
            request_body["year"] = year
        if month is not None:
            request_body["month"] = month
        if week is not None:
            request_body["week"] = week
        if day is not None:
            request_body["day"] = day

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="generate_report succeeded" if status_ok else "generate_report failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="generate_report failed: " + str(e)
            )

    async def list_settings(
        self
    ) -> ZammadResponse:
        """List settings

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/settings"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_settings succeeded" if status_ok else "list_settings failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_settings failed: " + str(e)
            )

    async def get_setting(
        self,
        name: str
    ) -> ZammadResponse:
        """Get setting

        Args:
            name: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/settings/{name}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_setting succeeded" if status_ok else "get_setting failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_setting failed: " + str(e)
            )

    async def update_setting(
        self,
        name: str,
        value: str
    ) -> ZammadResponse:
        """Update setting

        Args:
            name: str (required)
            value: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/settings/{name}"
        request_body: Dict = {}
        if value is not None:
            request_body["value"] = value

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_setting succeeded" if status_ok else "update_setting failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_setting failed: " + str(e)
            )

    async def get_monitoring_status(
        self,
        token: Optional[str] = None
    ) -> ZammadResponse:
        """Get monitoring health check

        Args:
            token: Optional[str] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/monitoring/health_check"
        params = {}
        if token is not None:
            params["token"] = token
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_monitoring_status succeeded" if status_ok else "get_monitoring_status failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_monitoring_status failed: " + str(e)
            )

    async def get_monitoring_amount_check(
        self,
        token: Optional[str] = None,
        period: Optional[int] = None
    ) -> ZammadResponse:
        """Get monitoring amount check

        Args:
            token: Optional[str] (optional)
            period: Optional[int] (optional)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/monitoring/amount_check"
        params = {}
        if token is not None:
            params["token"] = token
        if period is not None:
            params["period"] = period
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="get_monitoring_amount_check succeeded" if status_ok else "get_monitoring_amount_check failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="get_monitoring_amount_check failed: " + str(e)
            )

    async def list_translations(
        self,
        locale: str
    ) -> ZammadResponse:
        """List translations

        Args:
            locale: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/translations"
        params = {}
        if locale is not None:
            params["locale"] = locale
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="list_translations succeeded" if status_ok else "list_translations failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="list_translations failed: " + str(e)
            )

    async def update_translation(
        self,
        id: int,
        target: str
    ) -> ZammadResponse:
        """Update translation

        Args:
            id: int (required)
            target: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/translations/{id}"
        request_body: Dict = {}
        if target is not None:
            request_body["target"] = target

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="update_translation succeeded" if status_ok else "update_translation failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="update_translation failed: " + str(e)
            )

    async def export_tickets(
        self,
        query: str,
        format: str
    ) -> ZammadResponse:
        """Export tickets

        Args:
            query: str (required)
            format: str (required)

        Returns:
            ZammadResponse
        """
        url = f"{self.base_url}/api/v1/tickets/export"
        params = {}
        if query is not None:
            params["query"] = query
        if format is not None:
            params["format"] = format
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response = await self.http_client.execute(request)

            response_text = response.text()
            status_ok = response.status < SUCCESS_CODE_IS_LESS_THAN
            return ZammadResponse(
                success=status_ok,
                data=response.json() if response_text else None,
                message="export_tickets succeeded" if status_ok else "export_tickets failed"
            )
        except Exception as e:
            return ZammadResponse(
                success=False,
                error=str(e),
                message="export_tickets failed: " + str(e)
            )
