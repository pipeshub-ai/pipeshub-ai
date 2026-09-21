"""Who can see a knowledge base, for every way it can be shared -- asked as that person.

Every other suite searches as the org admin, who can reach everything, so a
sharing rule that was ignored would pass them all. Here an ordinary member of
the org searches, opens the collection and opens a file, and the answer is
compared with what the sharing says it should be.

Each row of ``SHAPES`` is one way access is given or taken away: shared with
the person, with a team they are in, with a team they are not in, with the
whole org; then revoked, the person removed from the team, the team deleted,
the person added to a team later. Adding a case is adding a row.

The collection holds files like the ones people really upload (see
``helper/realistic_files.py``): a multi-megabyte export, a three-sheet
workbook, a long Word document, a PDF, a messy CSV, non-English names, and a
file five folders deep. Each carries a made-up word found nowhere else, so a
search for it has one right answer. Two things follow from that:

* "can see it" means every one of those words finds its own file, as the
  person it was shared with -- not merely that some result came back;
* "cannot see it" means zero results, which is decided by the permission
  filter alone and does not depend on how search ranks anything.

Changes are checked on the very next request, not after a wait. Access is
resolved live for each search, so a revoked person still finding the files
even once is a leak, not a delay.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[3]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper import kb_sharing  # noqa: E402
from helper.clients.kb_client import KBClient  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.realistic_files import (  # noqa: E402
    REALISTIC_SLUGS,
    RealisticFile,
    realistic_corpus,
)
from helper.second_user import (  # noqa: E402
    NO_ACCESS_STATUSES,
    SecondUser,
    describe_search,
    has_no_access,
)
from messaging.test_e2e_record_pipeline import (  # noqa: E402
    TERMINAL_STATUSES,
    _extract_kb_id,
    _extract_record_id,
    _get_record_fields,
)
from retrieval.ranking import virtual_id_of  # noqa: E402

logger = logging.getLogger("permission-matrix")

# The large export takes longest; everything else is ready well before it.
INDEX_TIMEOUT_SEC = 900
INDEX_POLL_INTERVAL_SEC = 5
SEARCH_LIMIT = 10

# A general question, for the "cannot see it" direction: with no access, even
# a query that matches plenty must come back empty.
GENERAL_QUERY = "quarterly review budget owner"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.permissions,
    # Rows grant and revoke on one shared collection, so they run one at a time.
    pytest.mark.xdist_group("permission-matrix"),
]


def _plain_file(slug: str, token: str) -> RealisticFile:
    name = f"{slug}-{token}.md"
    body = f"# {slug}\n\nThis note exists so a test can find it by {token}.\n".encode()
    return RealisticFile(
        slug=slug, name=name, body=body, mimetype="text/markdown",
        tokens=(token,), upload_path=name,
    )


# A small plain file the matrix always relies on: if a realistic file fails to
# index, that is reported by its own test and must not turn every permission
# row red as well.
PROBE = _plain_file("probe", "tallowmere1177")
# Deleted by the deletion test, so kept apart from everything else.
DOOMED = _plain_file("doomed", "cinderwolk5530")


def stored_name(file_name: str) -> str:
    """The name PipesHub stores: the file name without its final extension.

    The extension is kept in its own field so the UI can show a type icon, so a
    record listed as "board-pack" for "board-pack.pdf" is correct, not a bug.
    Mirrors getFilenameWithoutExtension in libs/utils/file-extension.util.ts.
    """
    dot = file_name.rfind(".")
    return file_name if dot <= 0 or dot == len(file_name) - 1 else file_name[:dot]


def stored_extension(file_name: str) -> str | None:
    """The extension PipesHub stores: lower-cased, no dot, None when there is none."""
    dot = file_name.rfind(".")
    return None if dot <= 0 or dot == len(file_name) - 1 else file_name[dot + 1:].lower()


@dataclass
class IndexedFile:
    file: RealisticFile
    record_id: str | None = None
    status: str = "NOT_UPLOADED"
    virtual_id: str | None = None
    record_name: str | None = None
    record_extension: str | None = None
    upload_error: str | None = None

    @property
    def searchable(self) -> bool:
        return self.status == "COMPLETED" and bool(self.virtual_id)


@dataclass
class SharedCollection:
    kb_id: str
    files: dict[str, IndexedFile]

    @property
    def probe(self) -> IndexedFile:
        return self.files[PROBE.slug]

    @property
    def doomed(self) -> IndexedFile:
        return self.files[DOOMED.slug]

    def visible_files(self) -> list[IndexedFile]:
        """What a person with access must be able to find: every indexed file
        except the one the deletion test removes."""
        return [f for slug, f in self.files.items() if slug != DOOMED.slug and f.searchable]


def _upload(kb_client: KBClient, kb_id: str, entry: IndexedFile) -> None:
    file = entry.file
    try:
        response = kb_client.upload_file(
            kb_id, file.name, file.body, mimetype=file.mimetype, file_path=file.upload_path,
        )
        entry.record_id = _extract_record_id(response)
        entry.status = "UPLOADED"
    except Exception as exc:  # noqa: BLE001 - reported by the file's own test
        entry.upload_error = f"{type(exc).__name__}: {exc}"
        logger.warning("Upload of %s failed: %s", file.name, entry.upload_error)


def _wait_until_indexed(kb_client: KBClient, entries: list[IndexedFile]) -> None:
    """Poll every uploaded file until each reaches a final status."""
    pending = [e for e in entries if e.record_id]
    deadline = time.monotonic() + INDEX_TIMEOUT_SEC
    while pending and time.monotonic() < deadline:
        still = []
        for entry in pending:
            try:
                record = _get_record_fields(kb_client.get_record(entry.record_id))
            except Exception as exc:  # noqa: BLE001 - transient; try again next round
                logger.info("Reading record %s failed, retrying: %s", entry.record_id, exc)
                still.append(entry)
                continue
            entry.status = str(record.get("indexingStatus") or "UNKNOWN")
            entry.virtual_id = record.get("virtualRecordId") or entry.virtual_id
            entry.record_name = record.get("recordName") or entry.record_name
            file_record = record.get("fileRecord") or {}
            entry.record_extension = file_record.get("extension") or entry.record_extension
            if entry.status not in TERMINAL_STATUSES:
                still.append(entry)
        pending = still
        if pending:
            time.sleep(INDEX_POLL_INTERVAL_SEC)
    for entry in pending:
        entry.status = f"TIMED_OUT({entry.status})"


@pytest.fixture(scope="module")
def shared_collection(
    pipeshub_client: PipeshubClient, ai_models_configured
) -> Iterator[SharedCollection]:
    """A collection holding the probe, the doomed file and every realistic file.

    Owned by the admin and shared with nobody; each row shares it and undoes
    that itself.
    """
    del ai_models_configured  # ordering only: indexing needs an LLM and an embedding model
    kb_client = KBClient(pipeshub_client)
    kb_id = _extract_kb_id(kb_client.create_kb(name=f"permission-matrix-{uuid4().hex[:8]}"))
    assert kb_id, "KB create returned no id"

    files = {f.slug: IndexedFile(f) for f in (PROBE, DOOMED, *realistic_corpus())}
    try:
        for entry in files.values():
            _upload(kb_client, kb_id, entry)
        _wait_until_indexed(kb_client, list(files.values()))
        for entry in files.values():
            logger.info(
                "%-8s %-40s %8d bytes -> %s", entry.file.slug, entry.file.name,
                entry.file.size, entry.upload_error or entry.status,
            )
        for required in (PROBE.slug, DOOMED.slug):
            entry = files[required]
            assert entry.searchable, (
                f"The {required} file, a few lines of plain markdown, did not become "
                f"searchable ({entry.upload_error or entry.status}). Nothing in this "
                "module can be judged without it."
            )
        yield SharedCollection(kb_id=kb_id, files=files)
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask a failure
            logger.warning("Could not delete collection %s: %s", kb_id, exc)


# ---- what a person can see --------------------------------------------------


def _hit_ids(resp: requests.Response) -> list[str]:
    body = resp.json()
    hits = (body.get("searchResponse") or body).get("searchResults") or []
    return [vid for vid in (virtual_id_of(hit) for hit in hits) if vid]


def assert_can_see(user: SecondUser, collection: SharedCollection, why: str) -> None:
    """Every indexed file is findable by its own words, and openable."""
    for entry in collection.visible_files():
        for token in entry.file.tokens:
            resp = user.search(token, collection.kb_id, limit=SEARCH_LIMIT)
            assert resp.status_code == 200, (
                f"{why}: searching {token!r} failed: {describe_search(resp)}"
            )
            assert entry.virtual_id in _hit_ids(resp), (
                f"{why}: {entry.file.name!r} ({entry.file.slug}) was not found by "
                f"{token!r}, a word that appears only in that file. "
                f"Got {describe_search(resp)}"
            )

    kb_resp = user.get(f"/api/v1/knowledgeBase/{collection.kb_id}")
    assert kb_resp.status_code == 200, (
        f"{why}: the collection did not open: {kb_resp.status_code}: {kb_resp.text[:200]}"
    )
    record_resp = user.get(f"/api/v1/knowledgeBase/record/{collection.probe.record_id}")
    assert record_resp.status_code == 200, (
        f"{why}: a file in it did not open: {record_resp.status_code}: {record_resp.text[:200]}"
    )


def assert_cannot_see(user: SecondUser, collection: SharedCollection, why: str) -> None:
    """Nothing comes back, and neither the collection nor a file opens."""
    queries = [GENERAL_QUERY] + [
        token for entry in collection.visible_files() for token in entry.file.tokens
    ]
    for query in queries:
        resp = user.search(query, collection.kb_id, limit=SEARCH_LIMIT)
        assert has_no_access(resp), (
            f"{why}: searching {query!r} reached the collection's files. "
            f"Got {describe_search(resp)}"
        )

    kb_resp = user.get(f"/api/v1/knowledgeBase/{collection.kb_id}")
    assert kb_resp.status_code in NO_ACCESS_STATUSES, (
        f"{why}: the collection opened: {kb_resp.status_code}: {kb_resp.text[:200]}"
    )
    record_resp = user.get(f"/api/v1/knowledgeBase/record/{collection.probe.record_id}")
    assert record_resp.status_code in NO_ACCESS_STATUSES, (
        f"{why}: a file in it opened: {record_resp.status_code}: {record_resp.text[:200]}"
    )


# ---- the ways of sharing ----------------------------------------------------


@dataclass
class Row:
    """What one row has done, so the next step and the cleanup can undo it."""

    client: PipeshubClient
    kb_id: str
    user: SecondUser
    team_id: str | None = None
    undo: list[Callable[[], None]] = field(default_factory=list)

    def share_with_person(self) -> None:
        kb_sharing.grant(self.client, self.kb_id, user_ids=[self.user.user_id])
        self.undo.append(lambda: kb_sharing.revoke(
            self.client, self.kb_id, user_ids=[self.user.user_id], strict=False))

    def unshare_with_person(self) -> None:
        kb_sharing.revoke(self.client, self.kb_id, user_ids=[self.user.user_id])

    def make_team(self, *, with_person: bool) -> None:
        members = [self.user.user_id] if with_person else []
        self.team_id = kb_sharing.create_team(self.client, members)
        team_id = self.team_id
        self.undo.append(lambda: kb_sharing.delete_team(self.client, team_id, strict=False))

    def share_with_team(self, team_id: str | None = None) -> None:
        team_id = team_id or self.team_id
        assert team_id, "no team to share with"
        kb_sharing.grant(self.client, self.kb_id, team_ids=[team_id])
        self.undo.append(lambda: kb_sharing.revoke(
            self.client, self.kb_id, team_ids=[team_id], strict=False))

    def unshare_with_team(self, team_id: str | None = None) -> None:
        kb_sharing.revoke(self.client, self.kb_id, team_ids=[team_id or self.team_id])

    def add_person_to_team(self) -> None:
        kb_sharing.add_team_members(self.client, self.team_id, [self.user.user_id])

    def remove_person_from_team(self) -> None:
        kb_sharing.remove_team_members(self.client, self.team_id, [self.user.user_id])

    def delete_team(self) -> None:
        kb_sharing.delete_team(self.client, self.team_id)

    @property
    def whole_org(self) -> str:
        return kb_sharing.all_team_id(self.client.org_id)


@dataclass(frozen=True)
class Shape:
    id: str
    share: Callable[[Row], None]
    sees: bool
    then: Callable[[Row], None] | None = None
    sees_after: bool | None = None


def _team_with_person(row: Row) -> None:
    row.make_team(with_person=True)
    row.share_with_team()


def _team_without_person(row: Row) -> None:
    row.make_team(with_person=False)
    row.share_with_team()


SHAPES = [
    Shape("not-shared", share=lambda r: None, sees=False),
    Shape(
        "shared-with-the-person-then-unshared",
        share=Row.share_with_person, sees=True,
        then=Row.unshare_with_person, sees_after=False,
    ),
    Shape(
        "shared-with-their-team-then-they-leave-it",
        share=_team_with_person, sees=True,
        then=Row.remove_person_from_team, sees_after=False,
    ),
    Shape(
        "shared-with-their-team-then-unshared",
        share=_team_with_person, sees=True,
        then=lambda r: r.unshare_with_team(), sees_after=False,
    ),
    Shape(
        "shared-with-their-team-then-the-team-is-deleted",
        share=_team_with_person, sees=True,
        then=Row.delete_team, sees_after=False,
    ),
    Shape("shared-with-a-team-they-are-not-in", share=_team_without_person, sees=False),
    Shape(
        "shared-with-a-team-they-join-later",
        share=_team_without_person, sees=False,
        then=Row.add_person_to_team, sees_after=True,
    ),
    Shape(
        "shared-with-the-whole-org-then-unshared",
        share=lambda r: r.share_with_team(r.whole_org), sees=True,
        then=lambda r: r.unshare_with_team(r.whole_org), sees_after=False,
    ),
]


def _check(row_user: SecondUser, collection: SharedCollection, sees: bool, why: str) -> None:
    if sees:
        assert_can_see(row_user, collection, why)
    else:
        assert_cannot_see(row_user, collection, why)


class TestPermissionMatrix:
    @pytest.mark.parametrize("shape", SHAPES, ids=lambda s: s.id)
    def test_what_the_person_can_see(
        self,
        shape: Shape,
        pipeshub_client: PipeshubClient,
        shared_collection: SharedCollection,
        second_user: SecondUser,
    ) -> None:
        # A row that leaked access into this one would make a "cannot see"
        # pass or fail for the wrong reason, so every row starts from nothing.
        assert_cannot_see(second_user, shared_collection, f"{shape.id}: before sharing")

        row = Row(pipeshub_client, shared_collection.kb_id, second_user)
        try:
            shape.share(row)
            _check(second_user, shared_collection, shape.sees, f"{shape.id}: after sharing")
            if shape.then is not None:
                shape.then(row)
                _check(second_user, shared_collection, bool(shape.sees_after),
                       f"{shape.id}: after the change")
        finally:
            for undo in reversed(row.undo):
                undo()


class TestDeletedFile:
    def test_a_deleted_file_is_gone_for_people_it_was_shared_with(
        self,
        pipeshub_client: PipeshubClient,
        shared_collection: SharedCollection,
        second_user: SecondUser,
    ) -> None:
        """Deleting a file removes it from their search at once; the rest stays."""
        doomed = shared_collection.doomed
        token = doomed.file.tokens[0]
        row = Row(pipeshub_client, shared_collection.kb_id, second_user)
        try:
            row.share_with_person()
            before = second_user.search(token, shared_collection.kb_id, limit=SEARCH_LIMIT)
            assert before.status_code == 200 and doomed.virtual_id in _hit_ids(before), (
                "Precondition failed: the file to be deleted was not findable while "
                f"shared, so deleting it proves nothing. Got {describe_search(before)}"
            )

            KBClient(pipeshub_client).delete_record(doomed.record_id)

            after = second_user.search(token, shared_collection.kb_id, limit=SEARCH_LIMIT)
            assert has_no_access(after) or doomed.virtual_id not in _hit_ids(after), (
                f"A deleted file was still found. Got {describe_search(after)}"
            )
            probe = shared_collection.probe
            still = second_user.search(probe.file.tokens[0], shared_collection.kb_id,
                                       limit=SEARCH_LIMIT)
            assert still.status_code == 200 and probe.virtual_id in _hit_ids(still), (
                "Deleting one file hid another one in the same collection. "
                f"Got {describe_search(still)}"
            )
        finally:
            for undo in reversed(row.undo):
                undo()


# ---- the realistic files themselves ------------------------------------------


class TestRealisticFiles:
    """Each kind of file people upload is indexed, keeps its name, and every
    part of it -- each sheet, the last page, the middle of a long export -- can
    be found."""

    @pytest.mark.parametrize("slug", REALISTIC_SLUGS)
    def test_it_is_indexed(self, slug: str, shared_collection: SharedCollection) -> None:
        entry = shared_collection.files[slug]
        assert entry.upload_error is None, f"{entry.file.name!r} did not upload: {entry.upload_error}"
        assert entry.status == "COMPLETED", (
            f"{entry.file.name!r} ({entry.file.size} bytes) ended as {entry.status}, "
            "so nobody can find anything in it."
        )

    @pytest.mark.parametrize("slug", REALISTIC_SLUGS)
    def test_it_keeps_its_name(self, slug: str, shared_collection: SharedCollection) -> None:
        entry = shared_collection.files[slug]
        if not entry.record_id:
            pytest.skip(f"not uploaded: {entry.upload_error}")
        expected = stored_name(entry.file.name)
        assert entry.record_name == expected, (
            f"Uploaded as {entry.file.name!r}, listed as {entry.record_name!r}; "
            f"expected {expected!r}."
        )
        assert entry.record_extension == stored_extension(entry.file.name), (
            f"{entry.file.name!r} is listed with extension {entry.record_extension!r}, "
            f"expected {stored_extension(entry.file.name)!r}."
        )

    @pytest.mark.parametrize("slug", REALISTIC_SLUGS)
    def test_every_part_of_it_can_be_found(
        self, slug: str, pipeshub_client: PipeshubClient, shared_collection: SharedCollection
    ) -> None:
        entry = shared_collection.files[slug]
        if not entry.searchable:
            pytest.skip(f"not indexed ({entry.upload_error or entry.status}); see test_it_is_indexed")
        for token in entry.file.tokens:
            resp = requests.post(
                f"{pipeshub_client.base_url}/api/v1/search",
                headers=pipeshub_client._headers(),
                json={"query": token, "filters": {"kb": [shared_collection.kb_id]},
                      "limit": SEARCH_LIMIT},
                timeout=pipeshub_client.timeout_seconds,
            )
            assert resp.status_code == 200, f"search failed: {describe_search(resp)}"
            assert entry.virtual_id in _hit_ids(resp), (
                f"{token!r} is in {entry.file.name!r} and nowhere else, but searching "
                f"for it did not return that file. Got {describe_search(resp)}"
            )

    def test_a_non_ascii_name_survives_an_upload_without_a_path(
        self, pipeshub_client: PipeshubClient, shared_collection: SharedCollection
    ) -> None:
        """What the Projects page and any API client send: just the file.

        The name then comes only from the multipart header, which the upload
        middleware once decoded as Latin-1, turning "résumé" into "rÃ©sumÃ©".
        """
        name = f"Überblick — 概要 {uuid4().hex[:6]}.md"
        kb_client = KBClient(pipeshub_client)
        record_id = _extract_record_id(kb_client.upload_file(
            shared_collection.kb_id, name, b"# Overview\n", mimetype="text/markdown",
        ))
        record = _get_record_fields(kb_client.get_record(record_id))
        expected = stored_name(name)
        assert record.get("recordName") == expected, (
            f"Uploaded as {name!r}, listed as {record.get('recordName')!r}; "
            f"expected {expected!r}."
        )
        extension = (record.get("fileRecord") or {}).get("extension")
        assert extension == stored_extension(name), (
            f"Uploaded as {name!r}, listed with extension {extension!r}."
        )
