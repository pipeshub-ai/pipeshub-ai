# pyright: ignore-file

"""
Drupal Wiki on the simulator – what PipesHub streams, and what a reindex does
=============================================================================

The body of a page and the bytes of an attachment are read from the wiki when PipesHub streams a record (for indexing,
or for a person opening it); a page is rendered as its title, its body and its tags and categories. The expected text is
read from the simulator's own REST API with the same token, never from what the connector stored.

  TC-DWSIM-CONT-001  A streamed page is the wiki's title, body, categories and tags; a streamed attachment has the bytes
                     the wiki serves (markdown, csv, json and text files)
  TC-DWSIM-CONT-002  Odd attachment names (quotes, a backslash, non-Latin letters, a very long name, a path) and an
                     empty file and an empty page body stream and are stored under their own name
  TC-DWSIM-CONT-003  A page or attachment gone at the source: streaming it answers not found, and the record is kept
                     until a sync removes it
  TC-DWSIM-CONT-004  Reindex a page and an attachment after the wiki changed them: the stored revision moves to the
                     wiki's, nothing else changes
  TC-DWSIM-CONT-005  Reindex a record whose page is gone at the source: the record is kept, the connector keeps working
  TC-DWSIM-CONT-006  A markup-bearing page title and body: the stream escapes the title and keeps the body as the wiki
                     sent it
  TC-DWSIM-CONT-007  Reindex after access changed in the wiki: it changes nobody's access (only a sync does)
"""

import html

import pytest
import requests
from connectors.drupal_wiki.sim_instances import (
    ADMIN_EMAIL,
    ALICE,
    LEADERSHIP_SPACE,
    PEOPLE_SPACE,
    TOKEN,
    TWO_FILES_PAGE,
)
from connectors.drupal_wiki.sim_world import DrupalWorld, succeeded

API = "/api/rest/scope/api"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _wiki_json(w: DrupalWorld, path: str) -> dict:
    resp = requests.get(f"{w.sim.base_url}{API}{path}", headers=AUTH, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _wiki_bytes(w: DrupalWorld, attachment: int) -> bytes:
    resp = requests.get(f"{w.sim.base_url}{API}/attachment/{attachment}/download", headers=AUTH, timeout=30)
    resp.raise_for_status()
    return resp.content


def _stream(w: DrupalWorld, record_id: str) -> bytes:
    resp = w.client.stream_record(record_id)
    return b"".join(resp.iter_content(chunk_size=65536))


def _admin_reads_everything(w: DrupalWorld) -> None:
    """The tests stream as the org admin, whom the product lets read what the wiki lets their email read."""
    admin, = (r["user"] for r in w.mutate(("user_create", {"email": ADMIN_EMAIL, "first_name": "IT Admin"})))
    w.mutate(("member", {"space": PEOPLE_SPACE, "principal_type": "user", "principal_id": admin}),
             ("member", {"space": LEADERSHIP_SPACE, "principal_type": "user", "principal_id": admin}))


async def _start(w: DrupalWorld, **kwargs) -> None:
    assert await w.start(enable=False, **{k: v for k, v in kwargs.items() if k == "knobs"}) is None
    _admin_reads_everything(w)
    assert succeeded(await w.enable())


async def _record_id(w: DrupalWorld, external_id: str) -> str:
    rec = await w.record_for(external_id)
    assert rec is not None, f"{external_id} is not stored"
    return rec["id"]


@pytest.mark.integration
@pytest.mark.drupal_wiki_sim
@pytest.mark.asyncio(loop_scope="session")
class TestDrupalWikiSimContent:

    async def test_tc_dwsim_cont_001_pages_and_attachments_stream_what_the_wiki_serves(
            self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        await _start(w)
        for pid in (101, 105, 301):
            page = _wiki_json(w, f"/page/{pid}")
            streamed = _stream(w, await _record_id(w, f"page:{pid}")).decode()
            assert f"<h1>{html.escape(page['title'])}</h1>" in streamed, f"page {pid}: the title"
            assert page["body"] in streamed, f"page {pid}: the body"
            for term in (page.get("categories") or []) + (page.get("tags") or []):
                assert html.escape(term["name"]) in streamed, f"page {pid}: {term['name']}"
        for aid in (5000, 5001, 5002, 5003, 5004, 5005):
            want = _wiki_bytes(w, aid)
            assert want, f"attachment {aid} is empty in the wiki"
            assert _stream(w, await _record_id(w, f"attachment:{aid}")) == want, f"attachment {aid}: its bytes"

    async def test_tc_dwsim_cont_002_odd_names_and_empty_content(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert await w.start(enable=False) is None
        _admin_reads_everything(w)
        names = ['say "hi".txt', "back\\slash.txt", "日本語のメモ.txt", ("long-" * 60) + ".txt", "../../escape.txt",
                 "with space and (parens).txt"]
        added = w.mutate(*[("attachment_add", {"page": TWO_FILES_PAGE, "file_name": n, "content": f"body of {n}"})
                           for n in names],
                         ("attachment_add", {"page": TWO_FILES_PAGE, "file_name": "empty.txt", "content": ""}))
        empty_page, = (r["page"] for r in w.mutate(("page_create", {"space": 1, "title": "Empty page", "body": ""})))
        assert succeeded(await w.enable())
        await w.assert_matches_source("after a sync of odd file names and empty content")
        for add, name in zip(added, [*names, "empty.txt"], strict=True):
            aid = add["attachment"]
            rec = await w.record_for(f"attachment:{aid}")
            assert rec is not None and rec["recordName"] == name, f"attachment {aid} is named {rec and rec['recordName']!r}"
            assert _stream(w, rec["id"]) == _wiki_bytes(w, aid), f"attachment {aid} ({name}): its bytes"
        streamed = _stream(w, await _record_id(w, f"page:{empty_page}")).decode()
        assert "<h1>Empty page</h1>" in streamed

    async def test_tc_dwsim_cont_003_gone_at_the_source(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        await _start(w)
        page_rec, att_rec = await _record_id(w, "page:109"), await _record_id(w, "attachment:5004")
        w.mutate(("page_delete", {"id": 109}), ("attachment_delete", {"id": 5004}))
        for rec_id, what in ((page_rec, "page"), (att_rec, "attachment")):
            with pytest.raises(requests.HTTPError) as err:
                _stream(w, rec_id)
            assert err.value.response.status_code == 404, f"a {what} gone at the source answered {err.value.response.status_code}"
        assert await w.record_for("page:109") is not None, "streaming a gone page removed its record"

    async def test_tc_dwsim_cont_004_reindex_follows_the_wiki(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        await _start(w)
        page, att = await w.record_for("page:104"), await w.record_for("attachment:5002")
        w.mutate(("page_edit", {"id": 104, "body_extra": "<p>edited after the sync</p>"}),
                 ("attachment_update", {"id": 5002}))
        want_page = str(_wiki_json(w, "/page/104")["lastModified"])
        want_att = str(_wiki_json(w, "/attachment/5002")["lastModified"])
        assert want_page != page["externalRevisionId"] and want_att != att["externalRevisionId"]
        w.client.reindex_record(page["id"])
        w.client.reindex_record(att["id"])

        async def moved() -> bool:
            now = {r["externalRecordId"]: r for r in await w.records(parents=False)}
            return (now["page:104"]["externalRevisionId"] == want_page
                    and now["attachment:5002"]["externalRevisionId"] == want_att)

        from helper.graph_provider_utils import async_poll_until
        await async_poll_until(lambda: moved(), timeout=120, interval=3, description="the reindex to store the new revision")
        now = {r["externalRecordId"]: r for r in await w.records(parents=False)}
        assert now["page:104"]["id"] == page["id"] and now["attachment:5002"]["id"] == att["id"]

    async def test_tc_dwsim_cont_005_reindex_of_a_record_gone_at_the_source(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        await _start(w)
        page = await w.record_for("page:109")
        w.mutate(("page_delete", {"id": 109}))
        w.client.reindex_record(page["id"])
        w.sim.call("GET", "/_sim/calls")
        assert await w.record_for("page:109") is not None, "a reindex of a page gone at the source removed it"
        w.mutate(("page_edit", {"id": 101, "body_extra": "<p>still syncing</p>"}))
        assert succeeded(await w.sync())

    async def test_tc_dwsim_cont_006_markup_in_names_and_bodies(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert await w.start(enable=False) is None
        _admin_reads_everything(w)
        title = '<script>alert("x")</script> & <b>bold</b>'
        page, = (r["page"] for r in w.mutate(("page_create", {"space": 1, "title": title, "body": "<p>kept <b>as is</b></p>"})))
        assert succeeded(await w.enable())
        await w.assert_matches_source("after a sync of markup in a title")
        rec = await w.record_for(f"page:{page}")
        assert rec["recordName"] == title, "the title is stored as text, unchanged"
        streamed = _stream(w, rec["id"]).decode()
        assert "<script>" not in streamed.split("</h1>")[0], "the title reached the page as markup"
        assert "<p>kept <b>as is</b></p>" in streamed

    async def test_tc_dwsim_cont_007_reindex_changes_nobodys_access(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        await _start(w)
        before = await w.holdings()
        w.mutate(("member", {"space": LEADERSHIP_SPACE, "principal_type": "user", "principal_id": ALICE}))
        w.client.reindex_record((await w.record_for("page:501"))["id"])
        w.client.reindex_record((await w.record_for("page:301"))["id"])
        await w.assert_holdings_kept(before, "after reindexing while a role was added in the wiki")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the sync")
