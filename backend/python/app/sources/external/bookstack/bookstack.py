"""
BookStack API DataSource

Auto-generated comprehensive BookStack API client wrapper.
Covers all BookStack API endpoints with explicit type hints.

Generated from BookStack API documentation at:
https://bookstack.bassopaolo.com/api/docs
"""
import base64
from typing import Dict, List, Optional, Union

from app.sources.client.bookstack.bookstack import BookStackClient, BookStackResponse
from app.sources.client.http.http_request import HTTPRequest


class BookStackDataSource:
    """Comprehensive BookStack API client wrapper.
    Provides async methods for ALL BookStack API endpoints:
    CONTENT MANAGEMENT:
    - Attachments (list, create, read, update, delete)
    - Books (CRUD, exports: HTML, PDF, plaintext, markdown)
    - Chapters (CRUD, exports: HTML, PDF, plaintext, markdown)
    - Pages (CRUD, exports: HTML, PDF, plaintext, markdown)
    - Image Gallery (CRUD operations)
    - Shelves (CRUD operations)
    USER & PERMISSIONS:
    - Users (CRUD, invitation support)
    - Roles (CRUD, permissions management)
    - Content Permissions (granular access control)
    SEARCH & DISCOVERY:
    - Search (unified search across all content types)
    SYSTEM MANAGEMENT:
    - Recycle Bin (list, restore, permanently delete)
    - Audit Log (system activity tracking)
    All methods return BookStackResponse objects with standardized format.
    Every parameter matches BookStack official API documentation exactly.
    No Any types - all parameters are explicitly typed.
    Supports multipart/form-data for file uploads.
    """

    def __init__(self, client: BookStackClient) -> None:
        """Initialize with BookStackClient.
        Args:
            client: BookStackClient instance with authentication configured
        """
        self._client = client
        self.http = client.get_client()
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'BookStackDataSource':
        """Return the data source instance."""
        return self

    async def list_attachments(
        self,
        count: Optional[int] = None,
        offset: Optional[int] = None,
        sort: Optional[str] = None,
        filter: Optional[Dict[str, str]] = None
    ) -> BookStackResponse:
        """Get a listing of attachments visible to the user

        Args:
            count: Number of records to return (max 500)
            offset: Number of records to skip
            sort: Field to sort by with +/- prefix
            filter: Filters to apply

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}
        if count is not None:
            params["count"] = count
        if offset is not None:
            params["offset"] = offset
        if sort is not None:
            params["sort"] = sort
        if filter is not None:
            for key, value in filter.items():
                params[f'filter[{key}]'] = value

        url = self.base_url + "/api/attachments"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def create_attachment(
        self,
        name: str,
        uploaded_to: int,
        link: Optional[str] = None,
        file: Optional[bytes] = None
    ) -> BookStackResponse:
        """Create a new attachment. Use multipart/form-data for file uploads

        Args:
            name: Attachment name (required)
            uploaded_to: ID of the page to attach to (required)
            link: URL for link attachments
            file: File data for file attachments

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if name is not None:
            body["name"] = name
        if uploaded_to is not None:
            body["uploaded_to"] = uploaded_to
        if link is not None:
            body["link"] = link

        files: Dict[str, bytes] = {}
        if file is not None:
            files["file"] = file

        url = self.base_url + "/api/attachments"

        headers = dict(self.http.headers)
        # Note: multipart/form-data requests need special handling
        # The HTTPRequest should handle multipart encoding when files are present

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            query_params=params,
            body=body,
            files=files
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def get_attachment(
        self,
        attachment_id: int
    ) -> BookStackResponse:
        """Get details of a single attachment including content

        Args:
            attachment_id: Attachment ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/attachments/{attachment_id}".format(attachment_id=attachment_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def update_attachment(
        self,
        attachment_id: int,
        name: Optional[str] = None,
        uploaded_to: Optional[int] = None,
        link: Optional[str] = None,
        file: Optional[bytes] = None
    ) -> BookStackResponse:
        """Update an attachment. Use multipart/form-data for file updates

        Args:
            attachment_id: Attachment ID (required)
            name: Attachment name
            uploaded_to: ID of the page to attach to
            link: URL for link attachments
            file: File data for file attachments

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if name is not None:
            body["name"] = name
        if uploaded_to is not None:
            body["uploaded_to"] = uploaded_to
        if link is not None:
            body["link"] = link

        files: Dict[str, bytes] = {}
        if file is not None:
            files["file"] = file

        url = self.base_url + "/api/attachments/{attachment_id}".format(attachment_id=attachment_id)

        headers = dict(self.http.headers)
        # Note: multipart/form-data requests need special handling
        # The HTTPRequest should handle multipart encoding when files are present

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            query_params=params,
            body=body,
            files=files
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def delete_attachment(
        self,
        attachment_id: int
    ) -> BookStackResponse:
        """Delete an attachment

        Args:
            attachment_id: Attachment ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/attachments/{attachment_id}".format(attachment_id=attachment_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def list_books(
        self,
        count: Optional[int] = None,
        offset: Optional[int] = None,
        sort: Optional[str] = None,
        filter: Optional[Dict[str, str]] = None
    ) -> BookStackResponse:
        """Get a listing of books visible to the user

        Args:
            count: Number of records to return (max 500)
            offset: Number of records to skip
            sort: Field to sort by with +/- prefix
            filter: Filters to apply

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}
        if count is not None:
            params["count"] = count
        if offset is not None:
            params["offset"] = offset
        if sort is not None:
            params["sort"] = sort
        if filter is not None:
            for key, value in filter.items():
                params[f'filter[{key}]'] = value

        url = self.base_url + "/api/books"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )
        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def create_book(
        self,
        name: str,
        description: Optional[str] = None,
        description_html: Optional[str] = None,
        tags: Optional[List[Dict[str, str]]] = None,
        default_template_id: Optional[int] = None,
        image: Optional[bytes] = None
    ) -> BookStackResponse:
        """Create a new book. Use multipart/form-data for image upload

        Args:
            name: Book name (required)
            description: Plain text description
            description_html: HTML description
            tags: Tags array
            default_template_id: Default template page ID
            image: Cover image file

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if description_html is not None:
            body["description_html"] = description_html
        if tags is not None:
            body["tags"] = tags
        if default_template_id is not None:
            body["default_template_id"] = default_template_id

        files: Dict[str, bytes] = {}
        if image is not None:
            files["image"] = image

        url = self.base_url + "/api/books"

        headers = dict(self.http.headers)
        # Note: multipart/form-data requests need special handling
        # The HTTPRequest should handle multipart encoding when files are present

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            query_params=params,
            body=body,
            files=files
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def get_book(
        self,
        book_id: int
    ) -> BookStackResponse:
        """Get details of a single book including contents

        Args:
            book_id: Book ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/books/{book_id}".format(book_id=book_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def update_book(
        self,
        book_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        description_html: Optional[str] = None,
        tags: Optional[List[Dict[str, str]]] = None,
        default_template_id: Optional[int] = None,
        image: Optional[bytes] = None
    ) -> BookStackResponse:
        """Update a book. Use multipart/form-data for image upload

        Args:
            book_id: Book ID (required)
            name: Book name
            description: Plain text description
            description_html: HTML description
            tags: Tags array
            default_template_id: Default template page ID
            image: Cover image file

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if description_html is not None:
            body["description_html"] = description_html
        if tags is not None:
            body["tags"] = tags
        if default_template_id is not None:
            body["default_template_id"] = default_template_id

        files: Dict[str, bytes] = {}
        if image is not None:
            files["image"] = image

        url = self.base_url + "/api/books/{book_id}".format(book_id=book_id)

        headers = dict(self.http.headers)
        # Note: multipart/form-data requests need special handling
        # The HTTPRequest should handle multipart encoding when files are present

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            query_params=params,
            body=body,
            files=files
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def delete_book(
        self,
        book_id: int
    ) -> BookStackResponse:
        """Delete a book (typically sends to recycle bin)

        Args:
            book_id: Book ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/books/{book_id}".format(book_id=book_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def export_book_html(
        self,
        book_id: int
    ) -> BookStackResponse:
        """Export a book as a contained HTML file

        Args:
            book_id: Book ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/books/{book_id}/export/html".format(book_id=book_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def export_book_pdf(
        self,
        book_id: int
    ) -> BookStackResponse:
        """Export a book as a PDF file

        Args:
            book_id: Book ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/books/{book_id}/export/pdf".format(book_id=book_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            # PDF exports return binary data, not JSON
            return BookStackResponse(success=True, data={"content": base64.b64encode(response.bytes()).decode('utf-8'), "content_type": response.content_type})
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def export_book_plaintext(
        self,
        book_id: int
    ) -> BookStackResponse:
        """Export a book as a plain text file

        Args:
            book_id: Book ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/books/{book_id}/export/plaintext".format(book_id=book_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def export_book_markdown(
        self,
        book_id: int
    ) -> BookStackResponse:
        """Export a book as a markdown file

        Args:
            book_id: Book ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/books/{book_id}/export/markdown".format(book_id=book_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            # Markdown exports return text content, not JSON
            return BookStackResponse(success=True, data={"content": response.text(), "content_type": response.content_type})
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def list_chapters(
        self,
        count: Optional[int] = None,
        offset: Optional[int] = None,
        sort: Optional[str] = None,
        filter: Optional[Dict[str, str]] = None
    ) -> BookStackResponse:
        """Get a listing of chapters visible to the user

        Args:
            count: Number of records to return (max 500)
            offset: Number of records to skip
            sort: Field to sort by with +/- prefix
            filter: Filters to apply

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}
        if count is not None:
            params["count"] = count
        if offset is not None:
            params["offset"] = offset
        if sort is not None:
            params["sort"] = sort
        if filter is not None:
            for key, value in filter.items():
                params[f'filter[{key}]'] = value

        url = self.base_url + "/api/chapters"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def create_chapter(
        self,
        book_id: int,
        name: str,
        description: Optional[str] = None,
        description_html: Optional[str] = None,
        tags: Optional[List[Dict[str, str]]] = None,
        priority: Optional[int] = None,
        default_template_id: Optional[int] = None
    ) -> BookStackResponse:
        """Create a new chapter in a book

        Args:
            book_id: Parent book ID (required)
            name: Chapter name (required)
            description: Plain text description
            description_html: HTML description
            tags: Tags array
            priority: Chapter priority/order
            default_template_id: Default template page ID

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if book_id is not None:
            body["book_id"] = book_id
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if description_html is not None:
            body["description_html"] = description_html
        if tags is not None:
            body["tags"] = tags
        if priority is not None:
            body["priority"] = priority
        if default_template_id is not None:
            body["default_template_id"] = default_template_id

        url = self.base_url + "/api/chapters"

        headers = dict(self.http.headers)
        headers['Content-Type'] = 'application/json'

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            query_params=params,
            body=body
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def get_chapter(
        self,
        chapter_id: int
    ) -> BookStackResponse:
        """Get details of a single chapter including pages

        Args:
            chapter_id: Chapter ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/chapters/{chapter_id}".format(chapter_id=chapter_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def update_chapter(
        self,
        chapter_id: int,
        book_id: Optional[int] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        description_html: Optional[str] = None,
        tags: Optional[List[Dict[str, str]]] = None,
        priority: Optional[int] = None,
        default_template_id: Optional[int] = None
    ) -> BookStackResponse:
        """Update a chapter

        Args:
            chapter_id: Chapter ID (required)
            book_id: Parent book ID (to move chapter)
            name: Chapter name
            description: Plain text description
            description_html: HTML description
            tags: Tags array
            priority: Chapter priority/order
            default_template_id: Default template page ID

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if book_id is not None:
            body["book_id"] = book_id
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if description_html is not None:
            body["description_html"] = description_html
        if tags is not None:
            body["tags"] = tags
        if priority is not None:
            body["priority"] = priority
        if default_template_id is not None:
            body["default_template_id"] = default_template_id

        url = self.base_url + "/api/chapters/{chapter_id}".format(chapter_id=chapter_id)

        headers = dict(self.http.headers)
        headers['Content-Type'] = 'application/json'

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            query_params=params,
            body=body
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def delete_chapter(
        self,
        chapter_id: int
    ) -> BookStackResponse:
        """Delete a chapter (typically sends to recycle bin)

        Args:
            chapter_id: Chapter ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/chapters/{chapter_id}".format(chapter_id=chapter_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def export_chapter_html(
        self,
        chapter_id: int
    ) -> BookStackResponse:
        """Export a chapter as a contained HTML file

        Args:
            chapter_id: Chapter ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/chapters/{chapter_id}/export/html".format(chapter_id=chapter_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def export_chapter_pdf(
        self,
        chapter_id: int
    ) -> BookStackResponse:
        """Export a chapter as a PDF file

        Args:
            chapter_id: Chapter ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/chapters/{chapter_id}/export/pdf".format(chapter_id=chapter_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def export_chapter_plaintext(
        self,
        chapter_id: int
    ) -> BookStackResponse:
        """Export a chapter as a plain text file

        Args:
            chapter_id: Chapter ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/chapters/{chapter_id}/export/plaintext".format(chapter_id=chapter_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            # PDF exports return binary data, not JSON
            return BookStackResponse(success=True, data={"content": base64.b64encode(response.bytes()).decode('utf-8'), "content_type": response.content_type})
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def export_chapter_markdown(
        self,
        chapter_id: int
    ) -> BookStackResponse:
        """Export a chapter as a markdown file

        Args:
            chapter_id: Chapter ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/chapters/{chapter_id}/export/markdown".format(chapter_id=chapter_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            # Markdown exports return text content, not JSON
            return BookStackResponse(success=True, data={"content": response.text(), "content_type": response.content_type})
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def list_pages(
        self,
        count: Optional[int] = None,
        offset: Optional[int] = None,
        sort: Optional[str] = None,
        filter: Optional[Dict[str, str]] = None
    ) -> BookStackResponse:
        """Get a listing of pages visible to the user

        Args:
            count: Number of records to return (max 500)
            offset: Number of records to skip
            sort: Field to sort by with +/- prefix
            filter: Filters to apply

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, str] = {}
        if count is not None:
            params["count"] = str(count)
        if offset is not None:
            params["offset"] = str(offset)
        if sort is not None:
            params["sort"] = sort
        if filter is not None:
            for key, value in filter.items():
                params[f'filter[{key}]'] = value

        url = self.base_url + "/api/pages"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            # Markdown exports return text content, not JSON
            return BookStackResponse(success=True, data={"content": response.text(), "content_type": response.content_type})
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def create_page(
        self,
        name: str,
        book_id: Optional[int] = None,
        chapter_id: Optional[int] = None,
        html: Optional[str] = None,
        markdown: Optional[str] = None,
        tags: Optional[List[Dict[str, str]]] = None,
        priority: Optional[int] = None
    ) -> BookStackResponse:
        """Create a new page in a book or chapter

        Args:
            book_id: Parent book ID (required without chapter_id)
            chapter_id: Parent chapter ID (required without book_id)
            name: Page name (required)
            html: HTML content (required without markdown)
            markdown: Markdown content (required without html)
            tags: Tags array
            priority: Page priority/order

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if book_id is not None:
            body["book_id"] = book_id
        if chapter_id is not None:
            body["chapter_id"] = chapter_id
        if name is not None:
            body["name"] = name
        if html is not None:
            body["html"] = html
        if markdown is not None:
            body["markdown"] = markdown
        if tags is not None:
            body["tags"] = tags
        if priority is not None:
            body["priority"] = priority

        url = self.base_url + "/api/pages"

        headers = dict(self.http.headers)
        headers['Content-Type'] = 'application/json'

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            query_params=params,
            body=body
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def get_page(
        self,
        page_id: int
    ) -> BookStackResponse:
        """Get details of a single page including content

        Args:
            page_id: Page ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/pages/{page_id}".format(page_id=page_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def update_page(
        self,
        page_id: int,
        book_id: Optional[int] = None,
        chapter_id: Optional[int] = None,
        name: Optional[str] = None,
        html: Optional[str] = None,
        markdown: Optional[str] = None,
        tags: Optional[List[Dict[str, str]]] = None,
        priority: Optional[int] = None
    ) -> BookStackResponse:
        """Update a page

        Args:
            page_id: Page ID (required)
            book_id: Parent book ID (to move page)
            chapter_id: Parent chapter ID (to move page)
            name: Page name
            html: HTML content
            markdown: Markdown content
            tags: Tags array
            priority: Page priority/order

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if book_id is not None:
            body["book_id"] = book_id
        if chapter_id is not None:
            body["chapter_id"] = chapter_id
        if name is not None:
            body["name"] = name
        if html is not None:
            body["html"] = html
        if markdown is not None:
            body["markdown"] = markdown
        if tags is not None:
            body["tags"] = tags
        if priority is not None:
            body["priority"] = priority

        url = self.base_url + "/api/pages/{page_id}".format(page_id=page_id)

        headers = dict(self.http.headers)
        headers['Content-Type'] = 'application/json'

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            query_params=params,
            body=body
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def delete_page(
        self,
        page_id: int
    ) -> BookStackResponse:
        """Delete a page (typically sends to recycle bin)

        Args:
            page_id: Page ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/pages/{page_id}".format(page_id=page_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def export_page_html(
        self,
        page_id: int
    ) -> BookStackResponse:
        """Export a page as a contained HTML file

        Args:
            page_id: Page ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/pages/{page_id}/export/html".format(page_id=page_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def export_page_pdf(
        self,
        page_id: int
    ) -> BookStackResponse:
        """Export a page as a PDF file

        Args:
            page_id: Page ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/pages/{page_id}/export/pdf".format(page_id=page_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def export_page_plaintext(
        self,
        page_id: int
    ) -> BookStackResponse:
        """Export a page as a plain text file

        Args:
            page_id: Page ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/pages/{page_id}/export/plaintext".format(page_id=page_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            # PDF exports return binary data, not JSON
            return BookStackResponse(success=True, data={"content": base64.b64encode(response.bytes()).decode('utf-8'), "content_type": response.content_type})
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def export_page_markdown(
        self,
        page_id: int
    ) -> BookStackResponse:
        """Export a page as a markdown file

        Args:
            page_id: Page ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/pages/{page_id}/export/markdown".format(page_id=page_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            response_text = response.text()
            return BookStackResponse(success=True, data={'markdown': response_text})
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def list_images(
        self,
        count: Optional[int] = None,
        offset: Optional[int] = None,
        sort: Optional[str] = None,
        filter: Optional[Dict[str, str]] = None
    ) -> BookStackResponse:
        """Get a listing of images in the system

        Args:
            count: Number of records to return (max 500)
            offset: Number of records to skip
            sort: Field to sort by with +/- prefix
            filter: Filters to apply

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}
        if count is not None:
            params["count"] = count
        if offset is not None:
            params["offset"] = offset
        if sort is not None:
            params["sort"] = sort
        if filter is not None:
            for key, value in filter.items():
                params[f'filter[{key}]'] = value

        url = self.base_url + "/api/image-gallery"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def create_image(
        self,
        type: str,
        uploaded_to: int,
        name: Optional[str] = None,
        image: bytes = None
    ) -> BookStackResponse:
        """Create a new image. Must use multipart/form-data

        Args:
            type: Image type: gallery or drawio (required)
            uploaded_to: ID of the page to attach to (required)
            name: Image name (defaults to filename)
            image: Image file data (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if type is not None:
            body["type"] = type
        if uploaded_to is not None:
            body["uploaded_to"] = uploaded_to
        if name is not None:
            body["name"] = name

        files: Dict[str, bytes] = {}
        if image is not None:
            files["image"] = image

        url = self.base_url + "/api/image-gallery"

        headers = dict(self.http.headers)
        # Note: multipart/form-data requests need special handling
        # The HTTPRequest should handle multipart encoding when files are present

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            query_params=params,
            body=body,
            files=files
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def get_image(
        self,
        image_id: int
    ) -> BookStackResponse:
        """Get details of a single image

        Args:
            image_id: Image ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/image-gallery/{image_id}".format(image_id=image_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def update_image(
        self,
        image_id: int,
        name: Optional[str] = None,
        image: Optional[bytes] = None
    ) -> BookStackResponse:
        """Update image details or file. Use multipart/form-data for file updates

        Args:
            image_id: Image ID (required)
            name: Image name
            image: New image file data

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if name is not None:
            body["name"] = name

        files: Dict[str, bytes] = {}
        if image is not None:
            files["image"] = image

        url = self.base_url + "/api/image-gallery/{image_id}".format(image_id=image_id)

        headers = dict(self.http.headers)
        # Note: multipart/form-data requests need special handling
        # The HTTPRequest should handle multipart encoding when files are present

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            query_params=params,
            body=body,
            files=files
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def delete_image(
        self,
        image_id: int
    ) -> BookStackResponse:
        """Delete an image from the system

        Args:
            image_id: Image ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/image-gallery/{image_id}".format(image_id=image_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def search_all(
        self,
        query: str = None,
        page: Optional[int] = None,
        count: Optional[int] = None
    ) -> BookStackResponse:
        """Search across all content types (shelves, books, chapters, pages)

        Args:
            query: Search query string (required)
            page: Page number for pagination
            count: Number of results per page (max 100)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}
        if query is not None:
            params["query"] = query
        if page is not None:
            params["page"] = page
        if count is not None:
            params["count"] = count

        url = self.base_url + "/api/search"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def list_shelves(
        self,
        count: Optional[int] = None,
        offset: Optional[int] = None,
        sort: Optional[str] = None,
        filter: Optional[Dict[str, str]] = None
    ) -> BookStackResponse:
        """Get a listing of shelves visible to the user

        Args:
            count: Number of records to return (max 500)
            offset: Number of records to skip
            sort: Field to sort by with +/- prefix
            filter: Filters to apply

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}
        if count is not None:
            params["count"] = count
        if offset is not None:
            params["offset"] = offset
        if sort is not None:
            params["sort"] = sort
        if filter is not None:
            for key, value in filter.items():
                params[f'filter[{key}]'] = value

        url = self.base_url + "/api/shelves"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def create_shelf(
        self,
        name: str,
        description: Optional[str] = None,
        description_html: Optional[str] = None,
        books: Optional[List[int]] = None,
        tags: Optional[List[Dict[str, str]]] = None,
        image: Optional[bytes] = None
    ) -> BookStackResponse:
        """Create a new shelf. Use multipart/form-data for image upload

        Args:
            name: Shelf name (required)
            description: Plain text description
            description_html: HTML description
            books: Array of book IDs
            tags: Tags array
            image: Cover image file

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if description_html is not None:
            body["description_html"] = description_html
        if books is not None:
            body["books"] = books
        if tags is not None:
            body["tags"] = tags

        files: Dict[str, bytes] = {}
        if image is not None:
            files["image"] = image

        url = self.base_url + "/api/shelves"

        headers = dict(self.http.headers)
        # Note: multipart/form-data requests need special handling
        # The HTTPRequest should handle multipart encoding when files are present

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            query_params=params,
            body=body,
            files=files
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def get_shelf(
        self,
        shelf_id: int
    ) -> BookStackResponse:
        """Get details of a single shelf including books

        Args:
            shelf_id: Shelf ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/shelves/{shelf_id}".format(shelf_id=shelf_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def update_shelf(
        self,
        shelf_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        description_html: Optional[str] = None,
        books: Optional[List[int]] = None,
        tags: Optional[List[Dict[str, str]]] = None,
        image: Optional[bytes] = None
    ) -> BookStackResponse:
        """Update a shelf. Use multipart/form-data for image upload

        Args:
            shelf_id: Shelf ID (required)
            name: Shelf name
            description: Plain text description
            description_html: HTML description
            books: Array of book IDs
            tags: Tags array
            image: Cover image file

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if description_html is not None:
            body["description_html"] = description_html
        if books is not None:
            body["books"] = books
        if tags is not None:
            body["tags"] = tags

        files: Dict[str, bytes] = {}
        if image is not None:
            files["image"] = image

        url = self.base_url + "/api/shelves/{shelf_id}".format(shelf_id=shelf_id)

        headers = dict(self.http.headers)
        # Note: multipart/form-data requests need special handling
        # The HTTPRequest should handle multipart encoding when files are present

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            query_params=params,
            body=body,
            files=files
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def delete_shelf(
        self,
        shelf_id: int
    ) -> BookStackResponse:
        """Delete a shelf (typically sends to recycle bin)

        Args:
            shelf_id: Shelf ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/shelves/{shelf_id}".format(shelf_id=shelf_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def list_users(
        self,
        count: Optional[int] = None,
        offset: Optional[int] = None,
        sort: Optional[str] = None,
        filter: Optional[Dict[str, str]] = None
    ) -> BookStackResponse:
        """Get a listing of users in the system. Requires permission to manage users

        Args:
            count: Number of records to return (max 500)
            offset: Number of records to skip
            sort: Field to sort by with +/- prefix
            filter: Filters to apply

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, str] = {}  # Changed to Dict[str, str]
        if count is not None:
            params["count"] = str(count)  # Convert to string
        if offset is not None:
            params["offset"] = str(offset)  # Convert to string
        if sort is not None:
            params["sort"] = sort
        if filter is not None:
            for key, value in filter.items():
                params[f'filter[{key}]'] = value

        url = self.base_url + "/api/users"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def create_user(
        self,
        name: str,
        email: str,
        external_auth_id: Optional[str] = None,
        language: Optional[str] = None,
        password: Optional[str] = None,
        roles: Optional[List[int]] = None,
        send_invite: Optional[bool] = None
    ) -> BookStackResponse:
        """Create a new user. Requires permission to manage users

        Args:
            name: User name (required)
            email: User email address (required)
            external_auth_id: External authentication ID
            language: Language code (e.g., en, fr, de)
            password: User password (min 8 characters)
            roles: Array of role IDs
            send_invite: Send invitation email to user

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if name is not None:
            body["name"] = name
        if email is not None:
            body["email"] = email
        if external_auth_id is not None:
            body["external_auth_id"] = external_auth_id
        if language is not None:
            body["language"] = language
        if password is not None:
            body["password"] = password
        if roles is not None:
            body["roles"] = roles
        if send_invite is not None:
            body["send_invite"] = send_invite

        url = self.base_url + "/api/users"

        headers = dict(self.http.headers)
        headers['Content-Type'] = 'application/json'

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            query_params=params,
            body=body
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def get_user(
        self,
        user_id: int
    ) -> BookStackResponse:
        """Get details of a single user. Requires permission to manage users

        Args:
            user_id: User ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/users/{user_id}".format(user_id=user_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def update_user(
        self,
        user_id: int,
        name: Optional[str] = None,
        email: Optional[str] = None,
        external_auth_id: Optional[str] = None,
        language: Optional[str] = None,
        password: Optional[str] = None,
        roles: Optional[List[int]] = None
    ) -> BookStackResponse:
        """Update a user. Requires permission to manage users

        Args:
            user_id: User ID (required)
            name: User name
            email: User email address
            external_auth_id: External authentication ID
            language: Language code (e.g., en, fr, de)
            password: User password (min 8 characters)
            roles: Array of role IDs

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if name is not None:
            body["name"] = name
        if email is not None:
            body["email"] = email
        if external_auth_id is not None:
            body["external_auth_id"] = external_auth_id
        if language is not None:
            body["language"] = language
        if password is not None:
            body["password"] = password
        if roles is not None:
            body["roles"] = roles

        url = self.base_url + "/api/users/{user_id}".format(user_id=user_id)

        headers = dict(self.http.headers)
        headers['Content-Type'] = 'application/json'

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            query_params=params,
            body=body
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def delete_user(
        self,
        user_id: int,
        migrate_ownership_id: Optional[int] = None
    ) -> BookStackResponse:
        """Delete a user. Requires permission to manage users

        Args:
            user_id: User ID (required)
            migrate_ownership_id: User ID to migrate content ownership to

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if migrate_ownership_id is not None:
            body["migrate_ownership_id"] = migrate_ownership_id

        url = self.base_url + "/api/users/{user_id}".format(user_id=user_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
            query_params=params,
            body=body
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def list_roles(
        self,
        count: Optional[int] = None,
        offset: Optional[int] = None,
        sort: Optional[str] = None,
        filter: Optional[Dict[str, str]] = None
    ) -> BookStackResponse:
        """Get a listing of roles in the system. Requires permission to manage roles

        Args:
            count: Number of records to return (max 500)
            offset: Number of records to skip
            sort: Field to sort by with +/- prefix
            filter: Filters to apply

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}
        if count is not None:
            params["count"] = count
        if offset is not None:
            params["offset"] = offset
        if sort is not None:
            params["sort"] = sort
        if filter is not None:
            for key, value in filter.items():
                params[f'filter[{key}]'] = value

        url = self.base_url + "/api/roles"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def create_role(
        self,
        display_name: str,
        description: Optional[str] = None,
        mfa_enforced: Optional[bool] = None,
        external_auth_id: Optional[str] = None,
        permissions: Optional[List[str]] = None
    ) -> BookStackResponse:
        """Create a new role. Requires permission to manage roles

        Args:
            display_name: Role display name (required)
            description: Role description
            mfa_enforced: Enforce MFA for this role
            external_auth_id: External authentication ID
            permissions: Array of permission names

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if display_name is not None:
            body["display_name"] = display_name
        if description is not None:
            body["description"] = description
        if mfa_enforced is not None:
            body["mfa_enforced"] = mfa_enforced
        if external_auth_id is not None:
            body["external_auth_id"] = external_auth_id
        if permissions is not None:
            body["permissions"] = permissions

        url = self.base_url + "/api/roles"

        headers = dict(self.http.headers)
        headers['Content-Type'] = 'application/json'

        request = HTTPRequest(
            method="POST",
            url=url,
            headers=headers,
            query_params=params,
            body=body
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def get_role(
        self,
        role_id: int
    ) -> BookStackResponse:
        """Get details of a single role including permissions. Requires permission to manage roles

        Args:
            role_id: Role ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/roles/{role_id}".format(role_id=role_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            response_data = response.json()

            if 'error' in response_data:
                error_message = response_data.get('error', {}).get('message', 'Unknown API error')
                return BookStackResponse(success=False, error=error_message)

            return BookStackResponse(success=True, data=response_data)
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def update_role(
        self,
        role_id: int,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        mfa_enforced: Optional[bool] = None,
        external_auth_id: Optional[str] = None,
        permissions: Optional[List[str]] = None
    ) -> BookStackResponse:
        """Update a role. Requires permission to manage roles

        Args:
            role_id: Role ID (required)
            display_name: Role display name
            description: Role description
            mfa_enforced: Enforce MFA for this role
            external_auth_id: External authentication ID
            permissions: Array of permission names

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if display_name is not None:
            body["display_name"] = display_name
        if description is not None:
            body["description"] = description
        if mfa_enforced is not None:
            body["mfa_enforced"] = mfa_enforced
        if external_auth_id is not None:
            body["external_auth_id"] = external_auth_id
        if permissions is not None:
            body["permissions"] = permissions

        url = self.base_url + "/api/roles/{role_id}".format(role_id=role_id)

        headers = dict(self.http.headers)
        headers['Content-Type'] = 'application/json'

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            query_params=params,
            body=body
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def delete_role(
        self,
        role_id: int
    ) -> BookStackResponse:
        """Delete a role. Requires permission to manage roles

        Args:
            role_id: Role ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/roles/{role_id}".format(role_id=role_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def list_recycle_bin(
        self,
        count: Optional[int] = None,
        offset: Optional[int] = None,
        sort: Optional[str] = None,
        filter: Optional[Dict[str, str]] = None
    ) -> BookStackResponse:
        """Get listing of items in recycle bin. Requires permission to manage settings and permissions

        Args:
            count: Number of records to return (max 500)
            offset: Number of records to skip
            sort: Field to sort by with +/- prefix
            filter: Filters to apply

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}
        if count is not None:
            params["count"] = count
        if offset is not None:
            params["offset"] = offset
        if sort is not None:
            params["sort"] = sort
        if filter is not None:
            for key, value in filter.items():
                params[f'filter[{key}]'] = value

        url = self.base_url + "/api/recycle-bin"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def restore_recycle_bin_item(
        self,
        deletion_id: int
    ) -> BookStackResponse:
        """Restore an item from recycle bin. Requires permission to manage settings and permissions

        Args:
            deletion_id: Deletion ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/recycle-bin/{deletion_id}".format(deletion_id=deletion_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def destroy_recycle_bin_item(
        self,
        deletion_id: int
    ) -> BookStackResponse:
        """Permanently delete item from recycle bin. Requires permission to manage settings and permissions

        Args:
            deletion_id: Deletion ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/recycle-bin/{deletion_id}".format(deletion_id=deletion_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="DELETE",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def get_content_permissions(
        self,
        content_type: str,
        content_id: int
    ) -> BookStackResponse:
        """Read content-level permissions for an item. Content types: page, book, chapter, bookshelf

        Args:
            content_type: Content type: page, book, chapter, or bookshelf (required)
            content_id: Content item ID (required)

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        url = self.base_url + "/api/content-permissions/{content_type}/{content_id}".format(content_type=content_type, content_id=content_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def update_content_permissions(
        self,
        content_type: str,
        content_id: int,
        owner_id: Optional[int] = None,
        role_permissions: Optional[List[Dict[str, Union[int, bool]]]] = None,
        fallback_permissions: Optional[Dict[str, Union[bool, None]]] = None
    ) -> BookStackResponse:
        """Update content-level permissions for an item. Content types: page, book, chapter, bookshelf

        Args:
            content_type: Content type: page, book, chapter, or bookshelf (required)
            content_id: Content item ID (required)
            owner_id: New owner user ID
            role_permissions: Role permission overrides
            fallback_permissions: Fallback permissions configuration

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}

        body: Dict[str, Union[str, int, bool, List, Dict, None]] = {}
        if owner_id is not None:
            body["owner_id"] = owner_id
        if role_permissions is not None:
            body["role_permissions"] = role_permissions
        if fallback_permissions is not None:
            body["fallback_permissions"] = fallback_permissions

        url = self.base_url + "/api/content-permissions/{content_type}/{content_id}".format(content_type=content_type, content_id=content_id)

        headers = dict(self.http.headers)
        headers['Content-Type'] = 'application/json'

        request = HTTPRequest(
            method="PUT",
            url=url,
            headers=headers,
            query_params=params,
            body=body
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def list_audit_log(
        self,
        count: Optional[int] = None,
        offset: Optional[int] = None,
        sort: Optional[str] = None,
        filter: Optional[Dict[str, str]] = None
    ) -> BookStackResponse:
        """Get listing of audit log events. Requires permission to manage users and settings

        Args:
            count: Number of records to return (max 500)
            offset: Number of records to skip
            sort: Field to sort by with +/- prefix
            filter: Filters to apply

        Returns:
            BookStackResponse: Response object with success status and data/error
        """
        params: Dict[str, Union[str, int]] = {}
        if count is not None:
            params["count"] = count
        if offset is not None:
            params["offset"] = offset
        if sort is not None:
            params["sort"] = sort
        if filter is not None:
            for key, value in filter.items():
                params[f'filter[{key}]'] = value

        url = self.base_url + "/api/audit-log"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query=params,
            body=None
        )

        try:
            response = await self.http.execute(request)
            return BookStackResponse(success=True, data=response.json())
        except Exception as e:
            return BookStackResponse(success=False, error=str(e))

    async def get_api_info(self) -> BookStackResponse:
        """Get information about the BookStack API client.
        Returns:
            BookStackResponse: Information about available API methods
        """
        info = {
            'total_methods': 59,
            'base_url': self.base_url,
            'api_categories': [
                'Attachments (5 methods)',
                'Books (9 methods - CRUD + 4 export formats)',
                'Chapters (9 methods - CRUD + 4 export formats)',
                'Pages (9 methods - CRUD + 4 export formats)',
                'Image Gallery (5 methods)',
                'Search (1 method)',
                'Shelves (5 methods)',
                'Users (5 methods with invitation support)',
                'Roles (5 methods with permissions)',
                'Recycle Bin (3 methods)',
                'Content Permissions (2 methods)',
                'Audit Log (1 method)'
            ]
        }
        return BookStackResponse(success=True, data=info)
