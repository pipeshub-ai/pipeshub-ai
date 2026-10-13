"""What a person can reach as their account is added, locked, unlocked and deleted.

CTO list, Cleanup: "User deleted/added" and "User blocked/unblocked"; Access
Control List. Every check is made as that person, through the paths they use:
search, opening a record, the knowledge-base list and a chat answer's
citations. Graph edges are not read here; what matters is what the product
hands the person.

Two knowledge bases are made for the module, each with one note carrying a word
found nowhere else: one is shared with the person under test, the other with
nobody. "Sees exactly what they were given" then has a precise meaning: the
shared note is found, opened, listed and cited; the other is none of those.

Every person here is a fresh account made for the test and deleted after it.
The main admin account is only used to share, lock-check and unlock.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import logging
import time
from collections.abc import Iterator

import pytest
import requests

from helper import kb_sharing
from helper.access_probe import (
    ask_once,
    ask_until_cited,
    assert_no_chat_leak,
    listed_kb_ids,
    open_status,
    search,
)
from helper.kb_notes import Note, create_kb_with_note, delete_kb
from helper.pipeshub_client import PipeshubClient
from helper.second_user import (
    NO_ACCESS_STATUSES,
    SecondUser,
    create_second_user,
    delete_second_user,
    log_in,
)
from messaging.test_e2e_record_pipeline import poll_until

logger = logging.getLogger("user-lifecycle-access")

pytestmark = [pytest.mark.integration, pytest.mark.permissions]

KB_PREFIX = "it-user-lifecycle"
# authenticateWithPassword locks the account on the fifth wrong password.
WRONG_ATTEMPTS_TO_LOCK = 5
# The session check compares the lock time with the token's iat, which has
# one-second resolution; see security/test_credential_change_invalidation.py.
TOKEN_CLOCK_MARGIN_SEC = 3
# The account reaches the graph over the message broker after the delete call.
DEACTIVATION_TIMEOUT_SEC = 60


@dataclasses.dataclass(frozen=True)
class Corpus:
    shared: Note
    private: Note


@pytest.fixture(scope="module")
def corpus(pipeshub_client: PipeshubClient, ai_models_configured) -> Iterator[Corpus]:
    del ai_models_configured  # ordering only: indexing needs an LLM and an embedding model
    shared = create_kb_with_note(pipeshub_client, KB_PREFIX, "sharedlumen")
    try:
        private = create_kb_with_note(pipeshub_client, KB_PREFIX, "privatemarrow")
    except BaseException:
        delete_kb(pipeshub_client, shared.kb_id)
        raise
    try:
        yield Corpus(shared=shared, private=private)
    finally:
        delete_kb(pipeshub_client, shared.kb_id)
        delete_kb(pipeshub_client, private.kb_id)


@pytest.fixture
def person(pipeshub_client: PipeshubClient) -> Iterator[SecondUser]:
    """A brand-new member, granted nothing yet. Deleted after the test, if it still exists."""
    pipeshub_client._ensure_access_token()
    user = create_second_user(pipeshub_client)
    try:
        yield user
    finally:
        delete_second_user(pipeshub_client, user)


def _share(client: PipeshubClient, note: Note, user: SecondUser) -> None:
    kb_sharing.grant(client, note.kb_id, user_ids=[user.user_id])


def assert_reaches(user: SecondUser, note: Note, why: str) -> None:
    scoped = search(user, note.word, note.kb_id)
    assert scoped.found(note.virtual_id), (
        f"{why}: searching the shared knowledge base for {note.word!r} did not return "
        f"its note ({scoped.describe()})."
    )
    unscoped = search(user, note.word)
    assert unscoped.found(note.virtual_id), (
        f"{why}: a search across everything they can reach did not return the shared "
        f"note ({unscoped.describe()})."
    )
    status = open_status(user, note.record_id)
    assert status == 200, f"{why}: opening the shared note returned HTTP {status}."


def assert_cannot_reach(user: SecondUser, note: Note, why: str) -> None:
    for kb_id in (note.kb_id, None):
        outcome = search(user, note.word, kb_id)
        assert outcome.refused_or_missing(note.virtual_id), (
            f"{why}: searching {'its knowledge base' if kb_id else 'everything'} for "
            f"{note.word!r} gave {outcome.describe()}; the note must not come back, and "
            "an error does not prove it stayed hidden."
        )
    status = open_status(user, note.record_id)
    assert status in NO_ACCESS_STATUSES, f"{why}: opening the note returned HTTP {status}."


def _lock_by_wrong_passwords(user: SecondUser) -> None:
    """Lock the account the way it happens for real: repeated wrong passwords."""
    for attempt in range(WRONG_ATTEMPTS_TO_LOCK):
        _wrong_login(user, attempt)


def _wrong_login(user: SecondUser, attempt: int) -> None:
    session = requests.post(
        f"{user.base_url}/api/v1/userAccount/initAuth", json={"email": user.email}, timeout=30,
    )
    requests.post(
        f"{user.base_url}/api/v1/userAccount/authenticate",
        headers={"x-session-token": session.headers.get("x-session-token", ""),
                 "Content-Type": "application/json"},
        json={"method": "password", "credentials": {"password": f"Wrong{attempt}!Pass"},
              "email": user.email},
        timeout=30,
    )


def _wait_past_token_issue(token: str) -> None:
    """Wait until the clock is safely past the token's ``iat``.

    The session check drops a token only when the lock is recorded more than a
    second after the token was issued, and ``iat`` has one-second resolution.
    Locking sooner would test that window, not the lock.
    """
    payload = token.split(".")[1]
    issued = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["iat"]
    poll_until(
        lambda: time.time() > issued + TOKEN_CLOCK_MARGIN_SEC,
        timeout=TOKEN_CLOCK_MARGIN_SEC + 5, interval=0.5,
        description="the clock to pass the token's issue time",
    )


def _delete_through_the_api(client: PipeshubClient, user: SecondUser) -> None:
    """Delete the account the way an admin does, and nothing more.

    ``delete_second_user`` also removes the password row first, which would make
    "their password no longer signs in" true for the test's own reason. The
    fixture's teardown does that cleanup afterwards.
    """
    resp = requests.delete(
        f"{client.base_url}/api/v1/users/{user.user_id}",
        headers=client._headers(),
        timeout=client.timeout_seconds,
    )
    assert resp.status_code < 300, f"Deleting the account failed: HTTP {resp.status_code}: {resp.text[:200]}"


async def _wait_until_inactive(graph_provider, user_id: str) -> None:
    """Wait, bounded, until the graph shows the deleted user as inactive.

    Read from the graph itself: no API lists inactive users
    (``/users/graph/list`` returns active ones only), so an API that stops
    showing the user cannot tell "deactivated" from "not processed yet".
    """
    deadline = time.monotonic() + DEACTIVATION_TIMEOUT_SEC
    seen: object = None
    while time.monotonic() < deadline:
        user = await graph_provider.get_user_by_user_id(user_id)
        seen = None if user is None else user.get("isActive")
        if user is not None and seen is False:
            return
        await asyncio.sleep(2)
    raise AssertionError(
        f"The deleted user {user_id} was not marked inactive in the graph within "
        f"{DEACTIVATION_TIMEOUT_SEC}s (last isActive: {seen!r})."
    )


def _login_refused(user: SecondUser) -> bool:
    try:
        log_in(user.base_url, user.email, user.timeout)
    except Exception:  # noqa: BLE001 - any refusal is the answer we want
        return True
    return False


class TestANewMember:
    def test_sees_exactly_what_they_were_given(
        self, pipeshub_client: PipeshubClient, corpus: Corpus, person: SecondUser
    ) -> None:
        """A new account reaches the one knowledge base shared with it, and nothing else."""
        assert_cannot_reach(person, corpus.shared, "before anything is shared")
        _share(pipeshub_client, corpus.shared, person)

        assert_reaches(person, corpus.shared, "after the share")
        assert_cannot_reach(person, corpus.private, "the knowledge base never shared")

        shared_listed = listed_kb_ids(person, corpus.shared.kb_name)
        private_listed = listed_kb_ids(person, corpus.private.kb_name)
        assert shared_listed is not None and private_listed is not None, (
            "the knowledge-base list could not be read as the member"
        )
        assert corpus.shared.kb_id in shared_listed, "the shared knowledge base is not in their list"
        assert corpus.private.kb_id not in private_listed, "a knowledge base never shared is in their list"

    def test_chat_cites_what_they_were_given_and_nothing_else(
        self, pipeshub_client: PipeshubClient, corpus: Corpus, person: SecondUser
    ) -> None:
        _share(pipeshub_client, corpus.shared, person)

        cited = ask_until_cited(
            person, corpus.shared.question, corpus.shared.record_id, corpus.shared.virtual_id,
            kb_ids=[corpus.shared.kb_id],
        )
        assert cited.cites(corpus.shared.record_id, corpus.shared.virtual_id), (
            "Asked about the note in the knowledge base shared with them, the answer did "
            f"not cite it: {cited.describe()}"
        )

        assert_no_chat_leak(
            person, corpus.private.question, corpus.private.record_id, corpus.private.virtual_id,
            "the knowledge base never shared with them",
        )


class TestADeletedMember:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_loses_everything_and_their_token_is_refused(
        self, pipeshub_client: PipeshubClient, corpus: Corpus, person: SecondUser, graph_provider,
    ) -> None:
        _share(pipeshub_client, corpus.shared, person)
        assert_reaches(person, corpus.shared, "before the account is deleted")

        _delete_through_the_api(pipeshub_client, person)

        scoped = person.search(corpus.shared.word, corpus.shared.kb_id)
        assert scoped.status_code == 401, (
            f"A deleted account's token still searched: HTTP {scoped.status_code}."
        )
        status = open_status(person, corpus.shared.record_id)
        assert status == 401, f"A deleted account's token still opened a record: HTTP {status}."
        chat = ask_once(person, corpus.shared.question, [corpus.shared.kb_id])
        assert chat.status == 401, f"A deleted account's token still reached chat: {chat.describe()}"
        assert _login_refused(person), "A deleted account could still sign in with its password."

        await _wait_until_inactive(graph_provider, person.user_id)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_is_no_longer_listed_on_what_was_shared_with_them(
        self, pipeshub_client: PipeshubClient, corpus: Corpus, person: SecondUser, graph_provider,
    ) -> None:
        _share(pipeshub_client, corpus.shared, person)
        _delete_through_the_api(pipeshub_client, person)

        await _wait_until_inactive(graph_provider, person.user_id)

        resp = requests.get(
            f"{pipeshub_client.base_url}/api/v1/knowledgeBase/{corpus.shared.kb_id}/permissions",
            headers=pipeshub_client._headers(),
            timeout=pipeshub_client.timeout_seconds,
        )
        assert resp.status_code == 200, f"listing the sharing failed: HTTP {resp.status_code}"
        permissions = resp.json().get("permissions") or []
        still_listed = [
            p for p in permissions
            if p.get("userId") == person.user_id or str(p.get("email") or "").lower() == person.email
        ]
        assert not still_listed, (
            f"The deleted account is still listed on the knowledge base's sharing: {still_listed}"
        )


class TestALockedMember:
    def test_cannot_search_until_an_admin_unlocks_them(
        self, pipeshub_client: PipeshubClient, corpus: Corpus, person: SecondUser
    ) -> None:
        _share(pipeshub_client, corpus.shared, person)
        assert_reaches(person, corpus.shared, "before the lock")
        _wait_past_token_issue(person.token)

        _lock_by_wrong_passwords(person)
        assert _login_refused(person), (
            f"{WRONG_ATTEMPTS_TO_LOCK} wrong passwords did not lock the account: the right "
            "password still signs in."
        )
        locked = person.search(corpus.shared.word, corpus.shared.kb_id)
        assert locked.status_code == 401, (
            f"A locked account's session still searched: HTTP {locked.status_code}."
        )

        resp = requests.put(
            f"{pipeshub_client.base_url}/api/v1/users/{person.user_id}/unblock",
            headers=pipeshub_client._headers(),
            timeout=pipeshub_client.timeout_seconds,
        )
        assert resp.status_code == 200, f"The admin unlock failed: HTTP {resp.status_code}: {resp.text[:200]}"

        unlocked = dataclasses.replace(
            person, token=log_in(person.base_url, person.email, person.timeout)
        )
        assert_reaches(unlocked, corpus.shared, "after the admin unlocked the account")
        assert_cannot_reach(unlocked, corpus.private, "after the unlock, the knowledge base never shared")
