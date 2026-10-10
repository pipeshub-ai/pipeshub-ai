"""Journey J-07: consent.

Given user B attaches a file to a shared chat and user C can read the chat
When C opens the file without consent, with consent, and after C is removed
Then it is 404 without consent, readable with consent, and unreadable immediately after C is removed

Owning phase: PH-07 (80-implementation-plan section 5); PI-09, PI-10, PI-11, PH07-02, PH07-04, PH07-22.

What this lane covers, and where the other half is:
- Covered here, over real HTTP into the real Node API: how consent gets recorded (B's turn carries
  `filesShared` / `shareToolResults`, sent through the public routes), who may read what (Node's PDP,
  `POST /api/v1/authz/internal/check` with an `authz:check` service token, called the way Python calls it:
  the attachment record id from the turn row, `ownerUserId` = the uploader, `runId` for artifacts), and
  revocation (a collaborators DELETE is visible on the very next check: the preview/download path sends no
  `aclVersion`, so no Python-side cache sits in between).
- Not on this lane, because Python is the lane's scriptable fake: the record read routes asking Node's PDP
  (`stream_record`, `download_file`, `get_signed_url`, `get_record_by_id`, ...), the 404 an unconsented reader gets
  there, and the `aclVersion` cache. Those are Python unit tests: `tests/unit/modules/authz/test_node_pdp_client.py`,
  `test_chat_content_access.py`, `tests/unit/connectors/api/test_router_chat_content_access.py`.
"""

from __future__ import annotations

import dataclasses

import pytest

from helper.collab_stack import chats, collab
from helper.collab_stack.pdp import artifact_allowed, attachment_allowed, verdict
from helper.collab_stack.seeds import messages_of

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]


@pytest.fixture
def roster(stack, roster):  # noqa: ANN001, ANN201
    """The shared roster with a fresh owner: sharing is rate limited per sharer, so a journey does not spend the budget of the others."""
    return dataclasses.replace(roster, owner=collab.fresh_actor(stack, "J7"))


def attach(record_id: str) -> list[dict]:
    return [{"recordId": record_id, "recordName": f"{record_id}.md"}]


def shared_chat(api, owner, uploader, reader) -> str:  # noqa: ANN001
    """Owner's chat; B (uploader) is a writer, C (reader) a read collaborator."""
    chat = chats.create_chat(api, owner)
    chats.share_as_writer(api, owner, chat, uploader)
    assert chats.share(api, owner, chat, reader).status_code == 200
    return chat


def send(api, who, chat: str, **body):  # noqa: ANN001, ANN201
    resp = chats.send_message(api, who, chat, "see attached", **body)
    assert resp.status_code == 200, resp.text[:300]
    return resp.headers["X-Run-Id"]


def test_j07_file_is_hidden_without_consent_and_open_with_it(stack, api, roster) -> None:  # noqa: ANN001
    owner, b, c = roster.owner, roster.write_recipient, roster.read_recipient
    chat = shared_chat(api, owner, b, c)
    send(api, b, chat, attachments=attach("rec-private"))
    send(api, b, chat, attachments=attach("rec-shared"), filesShared=True)
    send(api, b, chat, attachments=attach("rec-explicit-no"), filesShared=False)

    turns = {m["attachments"][0]["recordId"]: m for m in messages_of(stack.db, chat) if m.get("attachments")}
    assert turns["rec-shared"]["filesShared"] is True and str(turns["rec-shared"]["authorUserId"]) == b.user_id
    assert not turns["rec-private"].get("filesShared")

    assert attachment_allowed(api, c, "rec-private", b, chat) is False
    assert attachment_allowed(api, c, "rec-explicit-no", b, chat) is False
    assert attachment_allowed(api, c, "rec-shared", b, chat) is True
    allowed = verdict(api, c, "chatAttachment", "rec-shared", b.user_id, conversation_id=chat)
    assert isinstance(allowed["aclVersion"], int)
    denied = verdict(api, c, "chatAttachment", "rec-private", b.user_id, conversation_id=chat)
    assert denied == {"allow": False, "aclVersion": None}
    # The conversation id is optional (an upload can predate its chat): the turn row finds the chat.
    assert attachment_allowed(api, c, "rec-shared", b) is True
    assert attachment_allowed(api, c, "rec-private", b) is False

    # Consent is the uploader's alone: B reads its own file through its OWNER edge (Python), the owner needs consent too.
    assert attachment_allowed(api, owner, "rec-private", b, chat) is False
    assert attachment_allowed(api, owner, "rec-shared", b, chat) is True
    assert attachment_allowed(api, roster.stranger, "rec-shared", b, chat) is False


def test_j07_removing_the_reader_revokes_the_file_on_the_next_check(stack, api, roster) -> None:  # noqa: ANN001
    owner, b, c = roster.owner, roster.write_recipient, roster.read_recipient
    chat = shared_chat(api, owner, b, c)
    send(api, b, chat, attachments=attach("rec-1"), filesShared=True)
    run_id = send(api, b, chat, shareToolResults=True)
    assert attachment_allowed(api, c, "rec-1", b, chat) is True
    assert artifact_allowed(api, c, "art-1", b, chat, run_id) is True

    assert collab.remove(api, owner, chat, c.user_id).status_code == 200

    assert attachment_allowed(api, c, "rec-1", b, chat) is False
    assert attachment_allowed(api, c, "rec-1", b) is False
    assert artifact_allowed(api, c, "art-1", b, chat, run_id) is False
    assert chats.get_chat(api, c, chat).status_code == 404
    assert attachment_allowed(api, b, "rec-1", b, chat) is True


def test_j07_artifact_follows_the_consent_of_the_turn_that_ran(stack, api, roster) -> None:  # noqa: ANN001
    owner, b, c = roster.owner, roster.write_recipient, roster.read_recipient
    chat = shared_chat(api, owner, b, c)
    consented = send(api, b, chat, shareToolResults=True)
    withheld = send(api, b, chat)
    refused = send(api, b, chat, shareToolResults=False)

    assert artifact_allowed(api, c, "art-1", b, chat, consented) is True
    assert artifact_allowed(api, c, "art-2", b, chat, withheld) is False
    assert artifact_allowed(api, c, "art-3", b, chat, refused) is False
    # STAGING and temporary artifacts never leave their creator, consent or not.
    assert artifact_allowed(api, c, "art-1", b, chat, consented, visibility="STAGING") is False
    assert artifact_allowed(api, c, "art-1", b, chat, consented, isTemporary=True) is False
    assert artifact_allowed(api, c, "art-1", b, chat, consented, visibility="USER", isTemporary=False) is True
    # No runId, an unknown one, or no conversation: a legacy or unattributable artifact is denied.
    assert artifact_allowed(api, c, "art-1", b, chat, None) is False
    assert artifact_allowed(api, c, "art-1", b, chat, "00000000-0000-4000-8000-000000000000") is False
    assert artifact_allowed(api, c, "art-1", b, None, consented) is False
    assert artifact_allowed(api, roster.stranger, "art-1", b, chat, consented) is False


def test_j07_artifact_run_id_from_another_chat_does_not_consent(stack, api, roster) -> None:  # noqa: ANN001
    owner, b, c = roster.owner, roster.write_recipient, roster.read_recipient
    consenting = shared_chat(api, owner, b, c)
    other = shared_chat(api, owner, b, c)
    run_id = send(api, b, consenting, shareToolResults=True)
    send(api, b, other)

    assert artifact_allowed(api, c, "art-1", b, consenting, run_id) is True
    assert artifact_allowed(api, c, "art-1", b, other, run_id) is False


def test_j07_a_grant_through_one_chat_never_opens_a_file_of_another(stack, api, roster) -> None:  # noqa: ANN001
    owner, b, c = roster.owner, roster.write_recipient, roster.read_recipient
    chat_y = shared_chat(api, owner, b, c)  # C reads this one
    chat_x = chats.create_chat(api, owner)  # C has no access here
    chats.share_as_writer(api, owner, chat_x, b)
    send(api, b, chat_y, attachments=attach("rec-y"), filesShared=True)
    send(api, b, chat_x, attachments=attach("rec-x"), filesShared=True)
    send(api, b, chat_x, attachments=attach("rec-both"), filesShared=True)
    send(api, b, chat_y, attachments=attach("rec-both"), filesShared=True)

    assert attachment_allowed(api, c, "rec-y", b, chat_y) is True
    # C's access to chat Y says nothing about chat X's file, named with either chat id or none.
    assert attachment_allowed(api, c, "rec-x", b, chat_x) is False
    assert attachment_allowed(api, c, "rec-x", b, chat_y) is False
    assert attachment_allowed(api, c, "rec-x", b) is False
    # Attached in both chats: allowed through the one C can read, and only when asked about that one (or none).
    assert attachment_allowed(api, c, "rec-both", b) is True
    assert attachment_allowed(api, c, "rec-both", b, chat_y) is True
    assert attachment_allowed(api, c, "rec-both", b, chat_x) is False


def test_j07_a_turn_cannot_consent_a_file_its_author_does_not_own(stack, api, roster) -> None:  # noqa: ANN001
    """PH07-22: B lists a record that A uploaded and ticks `filesShared`; the check is made on the uploader's id, so C is refused."""
    owner, b, c = roster.owner, roster.write_recipient, roster.read_recipient
    chat = shared_chat(api, owner, b, c)
    send(api, b, chat, attachments=attach("rec-of-owner"), filesShared=True)

    assert attachment_allowed(api, c, "rec-of-owner", b, chat) is True  # B really is the author of this turn
    assert attachment_allowed(api, c, "rec-of-owner", owner, chat) is False  # the record belongs to A, whose turn never consented
