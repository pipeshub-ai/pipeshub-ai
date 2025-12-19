from typing import Any

import httpx  # type: ignore


class HTTPResponse:
    """HTTP response wrapper for httpx.Response
    Args:
        response: The httpx response object
    """

    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    @property
    def status(self) -> int:
        """Get the status code of the response"""
        return self.response.status_code

    @property
    def headers(self) -> dict[str, str]:
        """Get the headers of the response"""
        return dict(self.response.headers)

    @property
    def url(self) -> str:
        """Get the URL of the response"""
        return str(self.response.url)

    @property
    def content_type(self) -> str:
        """Get the content type of the response"""
        return self.response.headers.get("content-type", "").split(";")[0].strip()

    @property
    def is_json(self) -> bool:
        """Check if the response is a JSON file"""
        return self.content_type == "application/json"

    @property
    def is_binary(self) -> bool:
        """Check if the response is a binary file"""
        return self.content_type == "application/octet-stream"

    def json(self) -> dict[str, Any]:
        """Parse data as JSON"""
        return self.response.json()

    def text(self) -> str:
        """Get data as text string"""
        return self.response.text

    def bytes(self) -> bytes:
        """Get raw bytes data"""
        return self.response.content

    def raise_for_status(self) -> None:
        """Raise an exception if the response status indicates an error"""
        self.response.raise_for_status()
