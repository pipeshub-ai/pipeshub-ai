"""Google errors as the client libraries raise them, for the Gmail helper tests."""

import json

from googleapiclient.errors import HttpError
from httplib2 import Response


def http_error(status: int, reason: str = "", message: str = "error") -> HttpError:
    body = {"error": {"code": status, "message": message,
                      "errors": [{"reason": reason, "message": message, "domain": "global"}]}}
    return HttpError(Response({"status": status}), json.dumps(body).encode(), uri="https://gmail.googleapis.com/x")
