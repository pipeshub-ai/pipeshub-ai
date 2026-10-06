"""Journey J-07 on the real Python services: consent through the connectors service's record read route and Node's PDP.

Given B uploads a file (through the real attachment route) and attaches it to a turn of A's shared chat that C can read
When C reads the record the way the agent does (``GET /api/v1/internal/records/{id}/content``, scope ``record:content``)
Then it is 404 without consent, readable with consent, and 404 again on the very next read after C is removed.

The Python half (``can_read_record`` -> ``NodePdpClient`` -> Node's ``/api/v1/authz/internal/check``) and the Node half run
together here; the fake lane only plays the Node half (``integration_test_j07_consent.py``). A second case does the same for an
artifact the model saved in B's turn (``shareToolResults``).
"""

from __future__ import annotations

import re

import pytest
import requests

from helper.collab_stack import chats, collab
from helper.collab_stack.fake_llm import llm_turn, tool_call
from helper.collab_stack.identity import Actor, Directory
from helper.collab_stack.real_python import SAVE_ARTIFACT, latest_tool_result, loop_calls, offers, stream_turn, upload_attachment

pytestmark = [
    pytest.mark.integration,
    pytest.mark.collab_chats,
    pytest.mark.collab_stack,
    pytest.mark.collab_real_python,
    pytest.mark.usefixtures("flag_on_for_module"),
]

CONTENT = "/api/v1/internal/records/{record_id}/content"


def read_status(stack, reader: Actor, record_id: str) -> int:  # noqa: ANN001
    """What the agent's record read answers for ``reader``: 200 with bytes, or 404 (denied and missing look the same)."""
    token = Directory.service_token(reader, ("record:content",))
    resp = requests.get(stack.python.connectors_url + CONTENT.format(record_id=record_id), headers={"Authorization": f"Bearer {token}"}, timeout=30)
    return resp.status_code


def upload(stack, who: Actor, name: str = "notes.txt") -> str:  # noqa: ANN001
    return upload_attachment(stack.node.base_url, who, name)


def attach(record_id: str) -> list[dict]:
    return [{"recordId": record_id, "recordName": "notes.txt"}]


@pytest.fixture
def world(stack, api):  # noqa: ANN001, ANN201
    """A's chat; B is a writer and C a reader."""
    owner, b, c = collab.fresh_actor(stack, "J7A"), collab.fresh_actor(stack, "J7B"), collab.fresh_actor(stack, "J7C")
    chat = chats.create_chat(api, owner)
    chats.share_as_writer(api, owner, chat, b)
    assert chats.share(api, owner, chat, c).status_code == 200
    return owner, b, c, chat


def send(api, who, chat: str, **body):  # noqa: ANN001, ANN201
    resp = chats.send_message(api, who, chat, "see attached", **body)
    assert resp.status_code == 200, resp.text[:300]
    return resp.headers["X-Run-Id"]


def test_j07_a_file_is_hidden_without_consent_open_with_it_and_closed_again_on_removal(stack, api, world) -> None:  # noqa: ANN001
    owner, b, c, chat = world
    private, shared = upload(stack, b, "private.txt"), upload(stack, b, "shared.txt")
    send(api, b, chat, attachments=attach(private))
    send(api, b, chat, attachments=attach(shared), filesShared=True)

    # The uploader reads its own file through the graph (OWNER edge); everyone else needs the turn's consent.
    assert read_status(stack, b, private) == 200
    assert read_status(stack, c, private) == 404
    assert read_status(stack, owner, private) == 404
    assert read_status(stack, c, shared) == 200
    assert read_status(stack, owner, shared) == 200
    assert read_status(stack, stack.roster.stranger, shared) == 404

    assert collab.remove(api, owner, chat, c.user_id).status_code == 200
    assert read_status(stack, c, shared) == 404, "revocation must be visible on the next read"


def test_j07_a_turn_that_refuses_consent_keeps_the_file_private(stack, api, world) -> None:  # noqa: ANN001
    owner, b, c, chat = world
    refused = upload(stack, b, "refused.txt")
    send(api, b, chat, attachments=attach(refused), filesShared=False)
    assert read_status(stack, c, refused) == 404
    assert read_status(stack, owner, refused) == 404


def test_j07_a_grant_through_one_chat_does_not_open_the_file_of_another(stack, api, world) -> None:  # noqa: ANN001
    owner, b, c, chat = world
    other = chats.create_chat(api, owner)  # C cannot read this one
    chats.share_as_writer(api, owner, other, b)
    elsewhere = upload(stack, b, "elsewhere.txt")
    send(api, b, other, attachments=attach(elsewhere), filesShared=True)
    assert read_status(stack, c, elsewhere) == 404


def test_j07_an_artifact_follows_the_consent_of_the_turn_that_ran(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, b, c, chat = world
    saved = {}
    for label, consent in (("consented", True), ("withheld", False)):
        mark = fake.mark()
        stream_turn(
            api, fake, b, chat, f"save {label}",
            llm_turn(tool_calls=[tool_call(SAVE_ARTIFACT, {"name": f"{label}.md", "content": f"# {label}"})], when=offers(SAVE_ARTIFACT)),
            llm_turn("Saved.", when=offers(SAVE_ARTIFACT)),
            shareToolResults=consent,
        )
        result = latest_tool_result(loop_calls(fake, mark, SAVE_ARTIFACT)[1])
        saved[label] = re.search(r'"artifact_id":\s*"([^"]+)"', result).group(1)  # type: ignore[union-attr]

    assert read_status(stack, b, saved["consented"]) == 200
    assert read_status(stack, c, saved["consented"]) == 200
    assert read_status(stack, c, saved["withheld"]) == 404
    assert collab.remove(api, owner, chat, c.user_id).status_code == 200
    assert read_status(stack, c, saved["consented"]) == 404
