"""A throwaway knowledge base holding one small note that search can find.

The access suites need "a record this person should (or should not) reach",
and nothing more. Each note carries a made-up word that appears nowhere else,
so a search for it has one right answer, and the note is only handed back once
the admin's own search returns it: a record that is merely uploaded, or marked
indexed a moment before the vector store has it, would make every "cannot see
it" check pass for the wrong reason.
"""

from __future__ import annotations

import logging
import random
import string
import uuid
from dataclasses import dataclass

import requests

from helper.clients.kb_client import KBClient
from helper.pipeshub_client import PipeshubClient
from messaging.test_e2e_record_pipeline import (
    TERMINAL_STATUSES,
    _extract_kb_id,
    _extract_record_id,
    _get_record_fields,
    poll_until,
)
from retrieval.ranking import virtual_id_of

logger = logging.getLogger("kb-notes")

INDEX_TIMEOUT_SEC = 600
INDEX_POLL_INTERVAL_SEC = 5
SEARCHABLE_TIMEOUT_SEC = 120
SEARCHABLE_POLL_INTERVAL_SEC = 3


@dataclass(frozen=True)
class Note:
    kb_id: str
    kb_name: str
    record_id: str
    virtual_id: str
    word: str

    @property
    def question(self) -> str:
        return f"What does the note about {self.word} say? Quote it."


def made_up_word(stem: str) -> str:
    """A word no document contains: a letter stem plus digits, e.g. ``vellomire4821``."""
    letters = "".join(random.choice(string.ascii_lowercase) for _ in range(4))
    return f"{stem}{letters}{random.randint(1000, 9999)}"


def _admin_finds(client: PipeshubClient, word: str, kb_id: str, virtual_id: str) -> bool:
    resp = requests.post(
        f"{client.base_url}/api/v1/search",
        headers=client._headers(),
        json={"query": word, "filters": {"kb": [kb_id]}, "limit": 10},
        timeout=client.timeout_seconds,
    )
    if resp.status_code != 200:
        return False
    body = resp.json()
    hits = (body.get("searchResponse") or body).get("searchResults") or []
    return any(virtual_id_of(hit) == virtual_id for hit in hits)


def create_kb_with_note(client: PipeshubClient, name_prefix: str, stem: str) -> Note:
    """Create a knowledge base named ``<name_prefix>-<random>`` with one indexed note.

    The caller deletes it with ``delete_kb``. If anything here fails the
    knowledge base is deleted before the error is raised.
    """
    kb_client = KBClient(client)
    kb_name = f"{name_prefix}-{uuid.uuid4().hex[:8]}"
    kb_id = _extract_kb_id(kb_client.create_kb(name=kb_name))
    assert kb_id, f"Creating knowledge base {kb_name} returned no id"
    try:
        word = made_up_word(stem)
        body = (
            f"# Note {word}\n\n"
            f"This note exists so an access test can find it by the word {word}.\n"
            f"The word {word} appears in this note and nowhere else.\n"
        ).encode()
        record_id = _extract_record_id(
            kb_client.upload_file(kb_id, f"{word}.md", body, mimetype="text/markdown")
        )
        assert record_id, f"Uploading the note to {kb_name} returned no record id"

        def _indexed() -> dict | None:
            record = _get_record_fields(kb_client.get_record(record_id))
            return record if record.get("indexingStatus") in TERMINAL_STATUSES else None

        record = poll_until(
            _indexed, timeout=INDEX_TIMEOUT_SEC, interval=INDEX_POLL_INTERVAL_SEC,
            description=f"note {record_id} to finish indexing",
        )
        status = record.get("indexingStatus")
        virtual_id = str(record.get("virtualRecordId") or "")
        assert status == "COMPLETED" and virtual_id, (
            f"The note in {kb_name} ended as {status!r} (virtual id {virtual_id!r}), so "
            "nobody can find it and no access check on it would mean anything."
        )
        poll_until(
            lambda: _admin_finds(client, word, kb_id, virtual_id),
            timeout=SEARCHABLE_TIMEOUT_SEC, interval=SEARCHABLE_POLL_INTERVAL_SEC,
            description=f"the admin's search to find the note by {word!r}",
        )
        return Note(kb_id=kb_id, kb_name=kb_name, record_id=record_id,
                    virtual_id=virtual_id, word=word)
    except BaseException:
        delete_kb(client, kb_id)
        raise


def delete_kb(client: PipeshubClient, kb_id: str) -> None:
    try:
        KBClient(client).delete_kb(kb_id)
    except Exception as exc:  # noqa: BLE001 - teardown must not hide the test's own failure
        logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)
