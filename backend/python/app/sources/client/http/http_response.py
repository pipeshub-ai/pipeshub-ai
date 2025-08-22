import json
from typing import Any, Dict

from aiohttp import ClientResponse  # type: ignore


class HTTPResponse:
    """HTTP response
    Args:
        data: The data of the response
        response: The response object
    """
    def __init__(self, data: bytes, response: ClientResponse) -> None:
        self.data = data
        self.response = response

    @property
    def status(self) -> int:
        """Get the status code of the response"""
        return self.response.status

    @property
    def headers(self) -> Dict[str, str]:
        """Get the headers of the response"""
        return dict(self.response.headers)

    @property
    def url(self) -> str:
        """Get the URL of the response"""
        return str(self.response.url)

    @property
    def content_type(self) -> str:
        """Get the content type of the response"""
        return self.response.content_type

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
        return json.loads(self.text())

    def text(self) -> str:
        """Get data as text string"""
        encoding = self.response.charset or 'utf-8'
        return self.data.decode(encoding, errors='replace')

    def bytes(self) -> bytes:
        """Get raw bytes data"""
        return self.data
