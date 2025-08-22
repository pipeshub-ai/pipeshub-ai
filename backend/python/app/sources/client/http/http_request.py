import json
from pathlib import Path
from typing import Any, Dict, Union

from pydantic import BaseModel, Field  # type: ignore


class HTTPRequest(BaseModel):
    """HTTP request
    Args:
        url: The URL of the request
        method: The HTTP method to use
        headers: The headers to send with the request
        body: The body of the request
        path_params: The path parameters to use
        query_params: The query parameters to use
    """
    url: str = Field(alias="uri")
    method: str = Field(default="GET")
    headers: Dict[str, str] = Field(default_factory=dict)
    body: Union[Dict[str, Any], bytes, Path, None] = None
    path_params: Dict[str, str] = Field(default_factory=dict, alias="path")
    query_params: Dict[str, str] = Field(default_factory=dict, alias="query")

    def to_json(self) -> str:
        """
        Convert request to a JSON string.
        Files are represented as their path, bytes are decoded as UTF-8.
        """
        data = self.model_dump()

        if isinstance(self.body, Path):
            data["body"] = str(self.body)
        elif isinstance(self.body, bytes):
            data["body"] = self.body.decode("utf-8", errors="replace")

        return json.dumps(data, indent=2)
