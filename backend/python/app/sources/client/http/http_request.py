import base64
import json
from typing import Any

from pydantic import BaseModel, Field  # type: ignore


class HTTPRequest(BaseModel):
    """HTTP request
    Args:
        url: The URL of the request
        method: The HTTP method to use
        headers: The headers to send with the request
        body: The body of the request (dict, bytes, or None)
        path_params: The path parameters to use
        query_params: The query parameters to use
    """

    url: str = Field(alias="url")
    method: str = Field(default="GET")
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] | bytes | None = None
    path_params: dict[str, str] = Field(default_factory=dict, alias="path")
    query_params: dict[str, str] = Field(default_factory=dict, alias="query")

    def to_json(self) -> str:
        """Convert request to a JSON string.
        Bytes are encoded as Base64 to preserve data integrity.
        """
        data = self.model_dump(by_alias=True)

        if isinstance(self.body, bytes):
            # Use Base64 encoding to preserve binary data integrity
            data["body"] = {
                "type": "bytes",
                "encoding": "base64",
                "data": base64.b64encode(self.body).decode("ascii"),
            }

        return json.dumps(data, indent=2)
