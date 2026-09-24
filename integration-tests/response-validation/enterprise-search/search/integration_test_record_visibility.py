"""What a *given user* can actually retrieve, and how fast that reacts to change.

Every other suite runs as the org admin, who can reach everything, so none of
them can tell "permission enforced" from "permission ignored". These do.

Written as assertions about the invariant -- a user without access retrieves
nothing -- rather than about any cache. That holds on every graph store, so the
tests stay honest whichever one is configured, and they start covering new
caching layers for free as those arrive.

Retrieval is a ranked, embedding-driven search, so the assertions are chosen to
not depend on ranking:

* the "can see it" direction asserts *some* hit came back, and fails **setup**
  loudly if the corpus cannot be found at all, so it can never pass vacuously;
* the "cannot see it" direction asserts **zero** hits, which is decided by the
  accessible-id filter alone -- an empty id set excludes everything no matter
  how the embeddings rank.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[3]
_RV_HELPER = _ROOT / "response-validation" / "helper"
for _p in (_ROOT, _RV_HELPER):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.kb_client import KBClient  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import (  # noqa: E402
    SecondUser,
    describe_search,
    has_no_access,
    search_result_count,
)
from messaging.test_e2e_record_pipeline import (  # noqa: E402
    TERMINAL_STATUSES,
    _extract_kb_id,
    _extract_record_id,
    _get_record_status,
    poll_until,
)

# Same readiness budget the session KB uses for the identical PDF.
INDEX_TIMEOUT_SEC = 180
INDEX_POLL_INTERVAL_SEC = 3

# The same question the rest of this suite searches for; it has answers in the
# Asana PDF that `session_kb` uploads and waits to be indexed.
SEARCH_QUERY = "every year asana undertakes which exercise?"

# These grant and revoke on the shared session KB, so they must not interleave
# with each other. Granting a *second* user changes nothing for the admin, so
# the rest of the suite is unaffected.
pytestmark = pytest.mark.xdist_group("record-visibility")


def _permissions_url(base_url: str, kb_id: str) -> str:
    return f"{base_url}/api/v1/knowledgeBase/{kb_id}/permissions"


def _grant_reader(client: PipeshubClient, kb_id: str, user: SecondUser) -> None:
    resp = requests.post(
        _permissions_url(client.base_url, kb_id),
        headers=client._headers(),
        json={"userIds": [user.user_id], "teamIds": [], "role": "READER"},
        timeout=client.timeout_seconds,
    )
    assert resp.status_code == 201, f"grant failed: {resp.status_code}: {resp.text}"
    granted = (resp.json().get("permissionResult") or {}).get("grantedCount") or 0
    # grantedCount 0 means the grant silently did nothing -- usually the user has
    # not reached the graph yet. Everything after this would then be measuring
    # the wrong thing.
    assert int(granted) >= 1, f"grant reported grantedCount=0: {resp.text}"


def _revoke(client: PipeshubClient, kb_id: str, user: SecondUser) -> requests.Response:
    return requests.delete(
        _permissions_url(client.base_url, kb_id),
        headers=client._headers(),
        json={"userIds": [user.user_id], "teamIds": []},
        timeout=client.timeout_seconds,
    )


@pytest.fixture
def granted_reader(
    pipeshub_client: PipeshubClient,
    session_kb: dict[str, str],
    second_user: SecondUser,
) -> Iterator[SecondUser]:
    """Second user holding READER on the session KB, revoked on teardown."""
    _grant_reader(pipeshub_client, session_kb["kb_id"], second_user)
    try:
        yield second_user
    finally:
        _revoke(pipeshub_client, session_kb["kb_id"], second_user)


@pytest.mark.integration
class TestRecordVisibility:
    """Grant and revoke must take effect on the next request, not eventually."""

    def test_granted_user_can_retrieve_kb_records(
        self,
        pipeshub_client: PipeshubClient,
        session_kb: dict[str, str],
        granted_reader: SecondUser,
    ) -> None:
        """A user just given READER retrieves the KB's content immediately."""
        kb_id = session_kb["kb_id"]

        resp = granted_reader.search(SEARCH_QUERY, kb_id)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert search_result_count(resp) > 0, (
            "Second user holds READER on this KB but retrieved nothing. Either the "
            "grant did not take effect, or a stale empty accessible-record set was "
            f"served. Response: {resp.text[:400]}"
        )

    def test_revoked_user_cannot_retrieve_kb_records(
        self,
        pipeshub_client: PipeshubClient,
        session_kb: dict[str, str],
        granted_reader: SecondUser,
    ) -> None:
        """Revoking access stops retrieval on the very next request.

        The regression this exists for: a user's accessible-record set was
        cached and only invalidated when records changed, never when
        *permissions* changed -- so a revoked user kept reading the KB until the
        entry aged out, minutes later.
        """
        kb_id = session_kb["kb_id"]

        before = granted_reader.search(SEARCH_QUERY, kb_id)
        assert before.status_code == 200, f"{before.status_code}: {before.text}"
        # Precondition, not the assertion under test. If the corpus cannot be
        # retrieved even *with* permission then the "0 results" check below
        # would pass for the wrong reason, so fail here instead.
        assert search_result_count(before) > 0, (
            "Precondition failed: the granted user retrieved nothing before "
            "revocation, so this test cannot prove revocation did anything. "
            f"Response: {before.text[:400]}"
        )

        revoke_resp = _revoke(pipeshub_client, kb_id, granted_reader)
        assert revoke_resp.status_code == 200, (
            f"revoke failed: {revoke_resp.status_code}: {revoke_resp.text}"
        )

        after = granted_reader.search(SEARCH_QUERY, kb_id)
        assert has_no_access(after), (
            "Revoked user could still reach this KB's records. Access was removed "
            "before this request was made, so the search should have been refused "
            f"or come back empty. Got {describe_search(after)}"
        )

    def test_user_without_permission_cannot_retrieve_kb_records(
        self,
        session_kb: dict[str, str],
        second_user: SecondUser,
    ) -> None:
        """A user never granted anything retrieves nothing -- the baseline the
        other two are measured against."""
        resp = second_user.search(SEARCH_QUERY, session_kb["kb_id"])
        assert has_no_access(resp), (
            "A user with no permission on this KB could reach its records: "
            f"{describe_search(resp)}"
        )


@pytest.mark.integration
class TestDeletedRecordVisibility:
    """A deleted record stops being retrievable straight away."""

    def test_deleted_record_disappears_from_retrieval(
        self,
        pipeshub_client: PipeshubClient,
        asana_pdf_blob: dict,
    ) -> None:
        """Upload, find it, delete it, and it is gone from the next search.

        Runs as the admin: this is about the record leaving the retrievable set,
        not about permissions. Builds its own KB rather than using `session_kb`,
        because deleting that one's only record would strand every other test in
        the suite.
        """
        kb_client = KBClient(pipeshub_client)
        kb_id, record_id = _kb_with_indexed_pdf(kb_client, asana_pdf_blob)
        try:
            search = _admin_search(pipeshub_client, SEARCH_QUERY, kb_id)
            assert search.status_code == 200, f"{search.status_code}: {search.text}"
            assert search_result_count(search) > 0, (
                "Precondition failed: freshly indexed record was not retrievable, "
                f"so its deletion proves nothing. Response: {search.text[:400]}"
            )

            delete_resp = kb_client.delete_record(record_id)
            assert delete_resp is not None, "delete_record returned nothing"

            after = _admin_search(pipeshub_client, SEARCH_QUERY, kb_id)
            # Deleting the KB's only record leaves nothing searchable, and the
            # service reports that as 404 ("No documents are available for you
            # to search yet") rather than an empty 200. Both mean the record is
            # gone; demanding 200 asserted an implementation detail instead.
            assert has_no_access(after), (
                "A deleted record was still retrievable. "
                f"Got {describe_search(after)}"
            )
        finally:
            try:
                kb_client.delete_kb(kb_id)
            except Exception:  # noqa: BLE001 - teardown must not fail the run
                pass


def _admin_search(
    client: PipeshubClient, query: str, kb_id: str, limit: int = 5
) -> requests.Response:
    return requests.post(
        f"{client.base_url}/api/v1/search",
        headers=client._headers(),
        json={"query": query, "filters": {"kb": [kb_id]}, "limit": limit},
        timeout=client.timeout_seconds,
    )


def _kb_with_indexed_pdf(
    kb_client: KBClient, blob: dict
) -> tuple[str, str]:
    """A private KB holding the same PDF, waited until it is really searchable.

    Waiting on the record *document* is not enough -- it exists well before it
    is indexed, and a search in that window comes back empty for reasons that
    have nothing to do with what is under test. Reuses the same readiness gate
    and terminal-status set as `session_kb`.
    """
    kb_resp = kb_client.create_kb(name="record-visibility-it-kb")
    kb_id = _extract_kb_id(kb_resp)
    assert kb_id, f"KB create returned no id: {kb_resp}"

    upload_resp = kb_client.upload_file(
        kb_id, blob["originalname"], blob["buffer"], mimetype=blob["mimetype"]
    )
    record_id = _extract_record_id(upload_resp)
    assert record_id, f"Upload returned no record id: {upload_resp}"

    poll_until(
        lambda: _get_record_status(kb_client.get_record(record_id)) in TERMINAL_STATUSES,
        timeout=INDEX_TIMEOUT_SEC,
        interval=INDEX_POLL_INTERVAL_SEC,
        description=f"record {record_id} to finish indexing",
    )
    status = _get_record_status(kb_client.get_record(record_id))
    assert status == "COMPLETED", (
        f"PDF reached terminal status {status!r}, expected COMPLETED — "
        "there would be nothing to retrieve, so the deletion check is meaningless."
    )
    return kb_id, record_id
