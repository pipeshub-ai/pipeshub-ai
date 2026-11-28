# pyright: ignore-file

"""
Drupal Wiki on the simulator – filters
======================================

The connector has one sync filter on spaces (in / not in), one on attachment types (in / not in) and two indexing
filters (pages, attachments) besides manual indexing. It has no date filter. The oracle takes the space filter as its
``include`` / ``exclude``; the attachment filter is applied to the oracle's attachments by the world.

  TC-DWSIM-FLT-001  Space filter in: only those spaces' pages and attachments are held; a page shared into a space
                    that is left out stays with its home space
  TC-DWSIM-FLT-002  Space filter not in
  TC-DWSIM-FLT-003  A filter changed later (saved on the disabled connector, which starts a full sync): a space added
                    is synced; a space left out is removed, and nobody reads it
  TC-DWSIM-FLT-004  Attachment type filter in / not in, and changed later
  TC-DWSIM-FLT-005  Indexing filters, pages off and attachments off: those records are stored and not indexed; the others are
  TC-DWSIM-FLT-006  Manual indexing: stored, not indexed
  TC-DWSIM-FLT-007  The space filter's options are the wiki's spaces, searchable and paged
"""

import pytest
from connectors.drupal_wiki.sim_instances import ENG_SPACE, LEADERSHIP_SPACE, PEOPLE_SPACE, PRODUCT_SPACE
from connectors.drupal_wiki.sim_world import (
    DrupalWorld,
    extension_filter,
    indexing_filter,
    space_filter,
    succeeded,
)

NOT_INDEXED = "AUTO_INDEX_OFF"


@pytest.mark.integration
@pytest.mark.drupal_wiki_sim
@pytest.mark.asyncio(loop_scope="session")
class TestDrupalWikiSimFilter:

    async def test_tc_dwsim_flt_001_space_filter_in(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(filters=space_filter("in", [ENG_SPACE, PEOPLE_SPACE])))
        await w.assert_matches_source("after the first sync of two spaces")
        w.mutate(("page_share", {"id": 301, "space": PRODUCT_SPACE}), ("page_create", {"space": 2, "title": "Outside"}),
                 ("page_create", {"space": ENG_SPACE, "title": "Inside"}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after pages were created in and out of the filter")

    async def test_tc_dwsim_flt_002_space_filter_not_in(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(filters=space_filter("not_in", [LEADERSHIP_SPACE, ENG_SPACE])))
        await w.assert_matches_source("after the first sync with two spaces left out")

    async def test_tc_dwsim_flt_003_filter_changed_later(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(filters=space_filter("in", [ENG_SPACE])))
        await w.assert_matches_source("after the first sync of Engineering")
        assert succeeded(await w.restart(space_filter("in", [ENG_SPACE, LEADERSHIP_SPACE])))
        await w.assert_matches_source("after Leadership was added to the filter")
        assert succeeded(await w.restart(space_filter("in", [LEADERSHIP_SPACE])))
        await w.assert_matches_source("after Engineering was taken out of the filter")
        assert succeeded(await w.restart(space_filter("not_in", [])))
        await w.assert_matches_source("after the filter was emptied")

    async def test_tc_dwsim_flt_004_attachment_type_filter(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start(filters=extension_filter("in", ["md"])))
        await w.assert_matches_source("after the first sync of markdown attachments")
        assert succeeded(await w.restart(extension_filter("not_in", ["md", "csv"])))
        await w.assert_matches_source("after every type but markdown and csv")
        w.mutate(("attachment_add", {"page": 105, "file_name": "NEW.MD", "content": "upper case extension"}),
                 ("attachment_add", {"page": 105, "file_name": "no-extension", "content": "none"}))
        assert succeeded(await w.sync())
        await w.assert_matches_source("after attachments with an upper-case extension and none were added")

    @pytest.mark.parametrize(("off", "other"), [("pages", "attachment:"), ("attachments", "page:")])
    async def test_tc_dwsim_flt_005_indexing_filters(self, drupal_world: DrupalWorld, off: str, other: str) -> None:
        w = drupal_world
        assert succeeded(await w.start(filters=indexing_filter(**{off: False})))
        await w.assert_matches_source(f"after the first sync with {off} not indexed")
        records = await w.records(parents=False)
        kept_off = [r for r in records if not r["externalRecordId"].startswith(other)]
        on = [r for r in records if r["externalRecordId"].startswith(other)]
        assert kept_off and all(r["indexingStatus"] == NOT_INDEXED for r in kept_off), \
            f"{off} must be stored and not indexed"
        assert on and all(r["indexingStatus"] != NOT_INDEXED for r in on), "the other kind must stay indexed"

    async def test_tc_dwsim_flt_006_manual_indexing(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        filters = {"indexing": {"values": {"enable_manual_sync": {"operator": "is", "type": "boolean", "value": True}}}}
        assert succeeded(await w.start(filters=filters))
        await w.assert_matches_source("after the first sync with manual indexing")
        records = await w.records(parents=False)
        assert records and all(r["indexingStatus"] == NOT_INDEXED for r in records), \
            f"manual indexing indexed {[r['externalRecordId'] for r in records if r['indexingStatus'] != NOT_INDEXED][:5]}"

    async def test_tc_dwsim_flt_007_filter_options(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        everything = w.client.get_filter_options(w.connector_id, "space_ids", page=1, limit=50)
        names = {o["label"] for o in everything["options"]}
        assert {"Engineering", "Product", "People Ops", "Company Handbook", "Leadership"} <= names
        first = w.client.get_filter_options(w.connector_id, "space_ids", page=1, limit=3)
        second = w.client.get_filter_options(w.connector_id, "space_ids", page=2, limit=3)
        assert len(first["options"]) == 3 and first.get("hasMore", True)
        assert {o["id"] for o in first["options"]}.isdisjoint({o["id"] for o in second["options"]})
        found = w.client.get_filter_options(w.connector_id, "space_ids", page=1, limit=20, search="People")
        assert [o["label"] for o in found["options"]] == ["People Ops"]
