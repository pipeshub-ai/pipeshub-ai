"""Every route that reads a record by id answers "exists, but you can't open it"
exactly as it answers "no record has this id", in status and in body. Otherwise
a refusal would tell a caller which ids are records, and the 404 would hide
nothing. Asked as a non-admin of a record in a knowledge base of the admin's own.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser

# Each route's path, and the query it is asked with. Any read-budget parameter
# sends the content read to the query service's record reader instead of the
# connector service's whole-record route, so both are asked.
_ROUTES = {
    "record-detail": ("/api/v1/knowledgeBase/record/{id}", {}),
    "record-stream": ("/api/v1/knowledgeBase/stream/record/{id}", {}),
    "record-content": ("/api/v1/connectors/record/{id}/content", {}),
    "record-content-budgeted": ("/api/v1/connectors/record/{id}/content", {"maxChars": 2000}),
}


def _answer(response: requests.Response, record_id: str) -> tuple[int, Any]:
    """Status and body, with what each answer carries of its own left out: the
    request id, and the id it was asked about, which some routes say back."""
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items() if k != "requestId"}
        if isinstance(value, str):
            return value.replace(record_id, "<id>")
        return value

    return response.status_code, scrub(body)


@pytest.mark.integration
@pytest.mark.parametrize("route", sorted(_ROUTES))
def test_a_record_the_caller_cannot_open_is_answered_as_a_missing_one(
    route: str, pipeshub_client: PipeshubClient, unopened_record: str, other_member: SecondUser
) -> None:
    path, params = _ROUTES[route]
    missing_id = str(uuid4())

    def ask(record_id: str) -> requests.Response:
        return requests.get(
            pipeshub_client.base_url + path.format(id=quote(record_id, safe="")),
            headers=other_member.headers,
            params=params,
            timeout=pipeshub_client.timeout_seconds,
        )

    hidden = _answer(ask(unopened_record), unopened_record)
    missing = _answer(ask(missing_id), missing_id)
    assert hidden[0] == 404, hidden
    assert hidden == missing
