# pyright: ignore-file

"""ServiceNow connector against a live instance: sync, hierarchy, versions, deletes, attachments,
content, lifecycle and filters.

The module seeds its own knowledge bases (``PHIT-<run>-...``), syncs them with one connector, then
changes them in rounds (every change of a round at once, one incremental sync) and checks each
change by external id: the article number for articles, the sys_id for attachments and groups.
Content is read as ``hr``, the persona the seeded KBs let read (an org admin is not a ServiceNow
reader, so PipesHub rightly shows them nothing). The TC ids are the ones README.md lists.

  order 1  TC-SYNC-01/02, TC-HIER-01/02/08, TC-ATT-01, TC-CONT-01..08/10/11   first full sync
  order 2  TC-SYNC-06                second full sync of unchanged data changes nothing
  order 3  TC-SYNC-03/04/05, TC-HIER-03..07, TC-VER-01/02/03, TC-DEL-01..05/07, TC-ATT-03/04/05
  order 4  TC-VER-04                 revert to an older version
  order 5  TC-LIFE-01/02/06, TC-DEL-06, TC-LIFE-03/05/07
  order 6+ TC-FILT-01..11/14         filters (skip until the connector registers them)
"""

from __future__ import annotations

import asyncio
import calendar
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.servicenow.constants import (
    CATEGORY_FILTER,
    DATE_FILTERS,
    INDEX_ARTICLES_FILTER,
    INDEX_ATTACHMENTS_FILTER,
    INDEX_TIMEOUT,
    KB_FILTER,
    NO_KB_FILTER,
    SYNC_TIMEOUT,
)
from connectors.servicenow.servicenow_helpers import (
    Persona,
    can_open,
    files_of,
    instance_host,
    kb_filter,
    merge_filters,
    poll,
    record,
    servicenow_connector,
    stream_text,
    sync,
    wait_absent,
    wait_file,
    wait_present,
)
from connectors.servicenow.servicenow_seed import (
    Article,
    ServiceNowSeed,
    readable_numbers,
    tiny_png,
)
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import apply_filter_full_sync, wait_for_sync_completion
from helper.indexing_wait import wait_until_record_indexing_completed
from helper.realistic_files import multi_page_docx, multi_page_pdf, multi_sheet_xlsx

pytestmark = [
    pytest.mark.integration,
    pytest.mark.servicenow,
    pytest.mark.asyncio(loop_scope="session"),
]

UNICODE_TITLE = "Ünïcødé — ✓ 漢字 مرحبا 🚀"
HTML_BODY = (
    "<h2>Ports</h2><table><tr><th>Service</th><th>Port</th></tr><tr><td>phitcell</td><td>8443</td></tr></table>"
    "<pre><code>phitcode = run()</code></pre><iframe src=\"https://example.com/embed\"></iframe>"
    "<script>alert('phitxss')</script><p>after the script</p>"
)


def _sn_ms(value: str) -> int:
    """A ServiceNow ``YYYY-MM-DD HH:MM:SS`` value (UTC) in epoch ms."""
    return calendar.timegm(time.strptime(value, "%Y-%m-%d %H:%M:%S")) * 1000


@dataclass
class World:
    seed: ServiceNowSeed
    connector_id: str
    reader: Persona
    outsider: Persona

    def art(self, key: str) -> Article:
        return self.seed.articles[key]


def _build(seed: ServiceNowSeed, p: dict[str, Persona]) -> None:
    readers = seed.group("readers", [p["hr"].sys_id])
    can_read = seed.criterion("uc-readers", group=readers)
    i1 = seed.kb("i1", read=[can_read])
    seed.kb("i2", read=[seed.criterion("uc-eng", user=p["eng"].sys_id)])
    i3 = seed.kb("i3", read=[can_read])
    cat_a = seed.category("cat-a", i1)
    sub = seed.category("sub-a1", cat_a, under_category=True)
    seed.category("cat-b", i1)
    cat_deact = seed.category("cat-deact", i1)
    cat_del = seed.category("cat-del", i1)
    # TC-HIER-08: a category whose parent was created after it.
    h8_child = seed.category("h8-child", i1)
    h8_parent = seed.category("h8-parent", i1)
    seed.rest.patch("kb_category", h8_child, {"parent_table": "kb_category", "parent_id": h8_parent})

    a = seed.article
    a("root", i1)
    art = a("a", i1, category=cat_a)
    for f in (multi_page_pdf(), multi_page_docx(), multi_sheet_xlsx()):
        seed.attach(art, f.name, f.body, f.mimetype)
    seed.attach(art, "phit-note.txt", f"Attachment note {seed.token('a-txt')}.".encode(), "text/plain")
    seed.attach(art, "phit-pixel.png", tiny_png(), "image/png")
    seed.attach(art, "phit-data.phitx", b"\x00\x01opaque", "application/x-phit-unknown")
    a("sub", i1, category=sub)
    a("h8", i1, category=h8_child)
    a("unicode", i1, title=f"{seed.name('unicode')} {UNICODE_TITLE}",
      text=f"<p>{UNICODE_TITLE} {seed.token('unicode')} שלום</p>")
    a("html", i1, text=HTML_BODY)
    a("empty", i1, text="")
    inline = a("inline", i1)
    img = seed.attach(inline, "inline.png", tiny_png(), "image/png", inline=True)
    seed.rest.patch("kb_knowledge", inline.sys_id, {
        "text": f"<p>{inline.token} image:</p><p><img src=\"/sys_attachment.do?sys_id={img}\"></p>"})
    seed.articles["inline"].attachments["__inline__"] = img
    a("wiki", i1, article_type="wiki", wiki=f"'''Wiki''' page {seed.token('wiki')}", text="")
    fb = a("feedback", i1)
    for key in ("m-body", "m-title", "m-move-cat", "m-move-kb", "m-retire", "m-draft", "m-inactive", "m-outdated",
                "m-delete", "m-expire", "m-ver-draft", "m-revert", "life-del"):
        a(key, i1, category=cat_a)
    for key in ("m-att-add", "m-att-rm", "m-att-replace"):
        seed.attach(a(key, i1), f"{key}.txt", f"{key} original {seed.token(key + '-v1')}".encode(), "text/plain")
    a("in-cat-deact", i1, category=cat_deact)
    a("in-cat-del", i1, category=cat_del)
    a("i2-1", seed.kbs["i2"])
    a("i3-1", i3)
    seed.publish_pending()
    seed.feedback(fb, p["hr"].sys_id, f"Helpful, thanks {seed.token('fb-comment')}")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def world(
    sn_seed: ServiceNowSeed,
    sn_personas: dict[str, Persona],
    sn_oauth_config: str,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncIterator[World]:
    await asyncio.to_thread(_build, sn_seed, sn_personas)
    scope = [sn_seed.kbs[k] for k in ("i1", "i2", "i3")]
    async with servicenow_connector(
        pipeshub_client, graph_provider, sn_oauth_config, name="servicenow-it",
        filters=kb_filter(scope), min_records=len(sn_seed.articles),
    ) as connector_id:
        yield World(sn_seed, connector_id, sn_personas["hr"], sn_personas["plain"])


# -- order 1: first full sync ---------------------------------------------------------------------


@pytest.mark.order(1)
async def test_tc_sync_01_article_fields(world: World, graph_provider: GraphProviderProtocol) -> None:
    art = world.art("a")
    row = world.seed.rest.one("kb_knowledge", f"sys_id={art.sys_id}", "number,short_description,sys_created_on,sys_updated_on")
    rec = await wait_present(graph_provider, world.connector_id, art.number)
    assert rec.record_name == row["short_description"]
    assert str(getattr(rec.record_type, "value", rec.record_type)) == "WEBPAGE"
    assert rec.source_created_at == _sn_ms(row["sys_created_on"]), "created time is not the article's, in UTC ms"
    assert rec.source_updated_at == _sn_ms(row["sys_updated_on"]), "updated time is not the article's, in UTC ms"
    assert rec.weburl and rec.weburl.startswith(instance_host()) and f"sysparm_article={art.number}" in rec.weburl


@pytest.mark.order(1)
async def test_tc_sync_02_counts_match_the_instance_by_kb(world: World, graph_provider: GraphProviderProtocol) -> None:
    rows = await graph_provider.fetch_records_by_type(world.connector_id, "WEBPAGE")
    synced = {r.get("externalRecordId") for r in rows}
    for key in ("i1", "i2", "i3"):
        expected = readable_numbers(world.seed.rest, [world.seed.kbs[key]])
        assert expected, f"the instance shows no articles in {key}"
        assert expected <= synced, f"{key}: missing {sorted(expected - synced)}"
    if KB_FILTER:
        everything = readable_numbers(world.seed.rest, [world.seed.kbs[k] for k in ("i1", "i2", "i3")])
        assert synced == everything, f"extra records: {sorted(synced - everything)}"


@pytest.mark.order(1)
async def test_tc_hier_01_02_08_tree_matches_the_instance(world: World, graph_provider: GraphProviderProtocol) -> None:
    s, cid = world.seed, world.connector_id
    kb = await graph_provider.get_record_group_by_external_id(cid, s.kbs["i1"])
    assert kb is not None and kb.name == s.name("i1")
    for child, parent in (("cat-a", s.kbs["i1"]), ("sub-a1", s.categories["cat-a"]),
                          ("h8-child", s.categories["h8-parent"]), ("h8-parent", s.kbs["i1"])):
        group = await graph_provider.get_record_group_by_external_id(cid, s.categories[child])
        assert group is not None, f"category {child} is not a record group"
        assert group.parent_external_group_id == parent, f"{child} hangs under {group.parent_external_group_id}"
    for key, group_id in (("a", s.categories["cat-a"]), ("sub", s.categories["sub-a1"]),
                          ("h8", s.categories["h8-child"]), ("root", s.kbs["i1"])):
        rec = await wait_present(graph_provider, cid, world.art(key).number)
        assert rec.external_record_group_id == group_id, f"{key} sits in {rec.external_record_group_id}"
    assert not await graph_provider.get_placeholder_records(cid), "a placeholder record was left behind"


@pytest.mark.order(1)
async def test_tc_att_01_attachments_are_files_under_their_article(world: World, graph_provider: GraphProviderProtocol) -> None:
    art = world.art("a")

    async def _all() -> dict[str, tuple[str, str]] | None:
        got = await files_of(graph_provider, world.connector_id, art.number)
        return got if set(art.attachments) <= set(got) else None
    files = await poll(_all, f"every attachment of {art.number} should be a file record under it", 300)
    assert set(files) == set(art.attachments), f"file records {sorted(files)}, attachments {sorted(art.attachments)}"
    for name in ("board-pack.pdf", "phit-note.txt"):
        rec = await wait_until_record_indexing_completed(
            graph_provider, world.connector_id, files[name][0], timeout=INDEX_TIMEOUT)
        assert rec.indexing_status == "COMPLETED", f"{name}: {rec.indexing_status}"


@pytest.mark.order(1)
async def test_tc_cont_01_article_stream_has_title_and_body(world: World, graph_provider: GraphProviderProtocol,
                                                             pipeshub_client: PipeshubClient) -> None:
    art = world.art("a")
    rec = await wait_present(graph_provider, world.connector_id, art.number)
    text = stream_text(pipeshub_client, world.reader.account, rec.id)
    assert art.title in text and art.token in text and art.number in text


@pytest.mark.order(1)
@pytest.mark.parametrize("key,must,must_not", [
    ("wiki", ("token",), ()),                                         # TC-CONT-02
    ("unicode", ("token", UNICODE_TITLE, "שלום"), ()),               # TC-CONT-03
    ("inline", ("token", "data:image/png;base64"), ()),               # TC-CONT-04/05
    ("empty", ("title",), ()),                                        # TC-CONT-06
    ("html", ("phitcell", "8443", "phitcode"), ("<script", "phitxss")),  # TC-CONT-07 / TC-SEC-04
    ("feedback", ("token", "fb-comment"), ()),                        # TC-CONT-11
])
async def test_tc_cont_stream_content(world: World, graph_provider: GraphProviderProtocol,
                                      pipeshub_client: PipeshubClient, key: str, must: tuple, must_not: tuple) -> None:
    art = world.art(key)
    rec = await wait_present(graph_provider, world.connector_id, art.number)
    assert rec.record_name == art.title
    text = stream_text(pipeshub_client, world.reader.account, rec.id)
    named = {"token": art.token, "title": art.title, "fb-comment": world.seed.token("fb-comment")}
    for piece in must:
        assert named.get(piece, piece) in text, f"{key}: {piece!r} is not in the streamed article"
    for piece in must_not:
        assert piece not in text, f"{key}: {piece!r} is in the streamed article"


@pytest.mark.order(1)
async def test_tc_cont_05_inline_image_is_not_a_file_record(world: World, graph_provider: GraphProviderProtocol) -> None:
    img = world.art("inline").attachments["__inline__"]
    await wait_present(graph_provider, world.connector_id, world.art("inline").number)
    assert await record(graph_provider, world.connector_id, img) is None
    assert not await files_of(graph_provider, world.connector_id, world.art("inline").number)


@pytest.mark.order(1)
async def test_tc_cont_08_unknown_mime_attachment_is_kept(world: World, graph_provider: GraphProviderProtocol) -> None:
    await wait_file(graph_provider, world.connector_id, world.art("a").number, "phit-data.phitx")


@pytest.mark.order(1)
async def test_tc_cont_10_citation_link_is_the_articles_portal_page(world: World, graph_provider: GraphProviderProtocol) -> None:
    """Opening it in a browser as a normal user stays a manual check (README)."""
    for key in ("a", "root", "unicode"):
        rec = await wait_present(graph_provider, world.connector_id, world.art(key).number)
        assert rec.weburl.startswith(instance_host() + "/") and rec.weburl.endswith(
            f"?id=kb_article_view&sysparm_article={world.art(key).number}"), rec.weburl


# -- order 2: nothing changed ---------------------------------------------------------------------


async def _snapshot(graph: GraphProviderProtocol, connector_id: str) -> dict[str, tuple[Any, Any]]:
    out = {}
    for kind in ("WEBPAGE", "FILE"):
        for r in await graph.fetch_records_by_type(connector_id, kind):
            out[r.get("externalRecordId")] = (r.get("_key") or r.get("id"), r.get("externalRevisionId"))
    return out


@pytest.mark.order(2)
async def test_tc_sync_06_second_full_sync_changes_nothing(world: World, pipeshub_client: PipeshubClient,
                                                           graph_provider: GraphProviderProtocol) -> None:
    before = await _snapshot(graph_provider, world.connector_id)
    await sync(pipeshub_client, graph_provider, world.connector_id, full=True)
    after = await _snapshot(graph_provider, world.connector_id)
    changed = {k: (before.get(k), after.get(k)) for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    assert not changed, f"a full sync of unchanged data changed records (id, revision): {changed}"


# -- order 3: one round of changes ------------------------------------------------------------------


def _draft_files(s: ServiceNowSeed, draft: str) -> dict[str, str]:
    return {r["file_name"]: r["sys_id"] for r in s.rest.get("sys_attachment", f"table_sys_id={draft}", "sys_id,file_name")}


def _new_version(s: ServiceNowSeed, key: str, remove: str | None = None, add: tuple[str, bytes] | None = None) -> None:
    """Check out ``key``, change its attachments on the draft, publish it."""
    art = s.articles[key]
    draft = s.checkout(art, publish=False)
    if remove:
        s.rest.delete_attachment(_draft_files(s, draft)[remove])
    if add:
        s.rest.upload("kb_knowledge", draft, add[0], add[1], "text/plain")
    s.publish([draft])
    art.sys_id, art.attachments = draft, _draft_files(s, draft)


def _mutate(s: ServiceNowSeed) -> None:
    """Round 1: one change per TC id, all at once."""
    a = s.articles
    s.article("new", s.kbs["i1"])                                                    # TC-SYNC-03
    s.publish_pending()
    s.checkout(a["m-body"], text=f"<p>Rewritten body {s.token('m-body-v2')}.</p>")    # TC-SYNC-04
    s.checkout(a["m-title"], short_description=s.name("m-title-renamed"))             # TC-SYNC-05
    s.checkout(a["m-move-cat"], kb_category=s.categories["cat-b"])                   # TC-HIER-03
    s.checkout(a["m-move-kb"], kb_knowledge_base=s.kbs["i2"], kb_category="")         # TC-HIER-04
    s.retire(a["m-retire"])                                                          # TC-DEL-01
    s.force(a["m-draft"].sys_id, workflow_state="draft")                            # TC-DEL-02
    s.force(a["m-inactive"].sys_id, active="false")                                 # TC-DEL-03
    s.force(a["m-outdated"].sys_id, workflow_state="outdated")                      # TC-DEL-04
    s.rest.delete("kb_knowledge", a["m-delete"].sys_id)                             # TC-DEL-05
    s.force(a["m-expire"].sys_id, valid_to="2020-01-01")                            # TC-DEL-07
    s.checkout(a["m-ver-draft"], publish=False, text=f"<p>{s.token('ver-draft')}</p>")  # TC-VER-03
    _new_version(s, "m-att-add", add=("m-att-added.txt", f"added {s.token('att-added')}".encode()))  # TC-ATT-03
    _new_version(s, "m-att-rm", remove="m-att-rm.txt")                              # TC-ATT-04
    _new_version(s, "m-att-replace", remove="m-att-replace.txt",                    # TC-ATT-05
                 add=("m-att-replace.txt", f"replaced {s.token('att-replace-v2')}".encode()))
    s.rest.patch("kb_knowledge_base", s.kbs["i3"], {"active": "false"})             # TC-HIER-06
    s.rest.patch("kb_category", s.categories["cat-deact"], {"active": "false"})     # TC-HIER-07
    s.delete_row("kb_category", s.categories["cat-del"])  # TC-HIER-05 (REST refuses: it has an article)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def round1(world: World, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol) -> dict[str, Any]:
    s, cid = world.seed, world.connector_id
    keys = ("m-body", "m-title", "m-move-cat", "m-move-kb", "m-ver-draft", "m-att-add", "m-att-rm", "m-att-replace",
            "in-cat-deact", "in-cat-del")
    before = {k: await wait_present(graph_provider, cid, world.art(k).number) for k in keys}
    # File records by name before the new versions: an unchanged file must keep its record.
    old_files = {k: {n: await wait_file(graph_provider, cid, world.art(k).number, n) for n in world.art(k).attachments}
                 for k in ("m-att-add", "m-att-rm", "m-att-replace")}
    # The whole old sentence: the new token ("...mbodyv2") contains the old one ("...mbody").
    old_body = {"m-body": f"The {world.art('m-body').token} note"}

    await asyncio.to_thread(_mutate, s)
    await sync(pipeshub_client, graph_provider, cid)
    return {"before": before, "old_files": old_files, "old_body": old_body}


@pytest.mark.order(3)
async def test_tc_sync_03_new_article_appears(world: World, round1: dict, graph_provider: GraphProviderProtocol) -> None:
    await wait_present(graph_provider, world.connector_id, world.art("new").number)


@pytest.mark.order(3)
async def test_tc_sync_04_tc_ver_01_02_body_edit_is_a_new_version_of_the_same_record(
    world: World, round1: dict, graph_provider: GraphProviderProtocol, pipeshub_client: PipeshubClient,
) -> None:
    before, art = round1["before"]["m-body"], world.art("m-body")
    rec = await wait_present(graph_provider, world.connector_id, art.number,
                             lambda r: r.external_revision_id != before.external_revision_id, "a new revision of m-body")
    assert rec.id == before.id, "the new version became a second record"
    rows = await graph_provider.fetch_records_by_type(world.connector_id, "WEBPAGE")
    assert sum(1 for r in rows if r.get("externalRecordId") == art.number) == 1
    rec = await wait_until_record_indexing_completed(graph_provider, world.connector_id, art.number, timeout=INDEX_TIMEOUT)
    text = stream_text(pipeshub_client, world.reader.account, rec.id)
    assert world.seed.token("m-body-v2") in text and round1["old_body"]["m-body"] not in text


@pytest.mark.order(3)
async def test_tc_sync_05_title_edit_renames_without_reindexing(world: World, round1: dict,
                                                                graph_provider: GraphProviderProtocol) -> None:
    before = round1["before"]["m-title"]
    rec = await wait_present(graph_provider, world.connector_id, world.art("m-title").number,
                             lambda r: r.record_name == world.seed.name("m-title-renamed"), "m-title renamed")
    assert rec.id == before.id
    assert rec.external_revision_id == before.external_revision_id, "a title-only edit changed the revision (re-index)"


@pytest.mark.order(3)
async def test_tc_hier_03_move_to_another_category(world: World, round1: dict, graph_provider: GraphProviderProtocol) -> None:
    before, target = round1["before"]["m-move-cat"], world.seed.categories["cat-b"]
    rec = await wait_present(graph_provider, world.connector_id, world.art("m-move-cat").number,
                             lambda r: r.external_record_group_id == target, "m-move-cat in cat-b")
    assert rec.id == before.id and rec.external_revision_id == before.external_revision_id


@pytest.mark.order(3)
async def test_tc_hier_04_move_to_another_kb_takes_its_permissions(
    world: World, round1: dict, graph_provider: GraphProviderProtocol, sn_personas: dict[str, Persona],
) -> None:
    target = world.seed.kbs["i2"]
    rec = await wait_present(graph_provider, world.connector_id, world.art("m-move-kb").number,
                             lambda r: r.external_record_group_id == target, "m-move-kb in i2")
    eng, hr = sn_personas["eng"].account, world.reader.account

    async def _moved() -> bool:
        return (await asyncio.to_thread(can_open, eng, rec.id)) is True and (await asyncio.to_thread(can_open, hr, rec.id)) is False
    await poll(_moved, "after the move eng (i2's reader) should open it and hr (i1's) should not")


@pytest.mark.order(3)
async def test_tc_hier_05_deleted_category_leaves_its_article_under_the_kb(world: World, round1: dict,
                                                                           graph_provider: GraphProviderProtocol) -> None:
    s = world.seed
    await wait_present(graph_provider, world.connector_id, world.art("in-cat-del").number,
                       lambda r: r.external_record_group_id == s.kbs["i1"], "in-cat-del under its KB")

    async def _group_gone() -> bool:
        return await graph_provider.get_record_group_by_external_id(world.connector_id, s.categories["cat-del"]) is None
    await poll(_group_gone, "the deleted category should be gone")


@pytest.mark.order(3)
async def test_tc_hier_06_deactivated_kb_takes_its_articles_away(world: World, round1: dict,
                                                                 graph_provider: GraphProviderProtocol) -> None:
    await wait_absent(graph_provider, world.connector_id, world.art("i3-1").number)


@pytest.mark.order(3)
async def test_tc_hier_07_article_in_a_deactivated_category_stays(world: World, round1: dict,
                                                                  graph_provider: GraphProviderProtocol) -> None:
    """ServiceNow still shows such an article in search and the portal (audit truth table)."""
    rec = await record(graph_provider, world.connector_id, world.art("in-cat-deact").number)
    assert rec is not None and rec.id == round1["before"]["in-cat-deact"].id


@pytest.mark.order(3)
async def test_tc_ver_03_a_new_draft_version_is_not_served(world: World, round1: dict, graph_provider: GraphProviderProtocol,
                                                           pipeshub_client: PipeshubClient) -> None:
    art, before = world.art("m-ver-draft"), round1["before"]["m-ver-draft"]
    rec = await wait_present(graph_provider, world.connector_id, art.number)
    assert rec.id == before.id and rec.external_revision_id == before.external_revision_id
    text = stream_text(pipeshub_client, world.reader.account, rec.id)
    assert art.token in text and world.seed.token("ver-draft") not in text


@pytest.mark.order(3)
@pytest.mark.parametrize("key", ["m-retire", "m-draft", "m-inactive", "m-outdated", "m-delete", "m-expire"])
async def test_tc_del_01_to_05_07_article_that_search_no_longer_shows_is_removed(
    world: World, round1: dict, graph_provider: GraphProviderProtocol, key: str,
) -> None:
    """Retired, back to draft, inactive, outdated, deleted, past valid_to: gone on an incremental sync."""
    await wait_absent(graph_provider, world.connector_id, world.art(key).number)


@pytest.mark.order(3)
@pytest.mark.parametrize("key,kept,new,gone", [
    ("m-att-add", ("m-att-add.txt",), ("m-att-added.txt",), ()),  # TC-ATT-03: added; the copied file keeps its record
    ("m-att-rm", (), (), ("m-att-rm.txt",)),                       # TC-ATT-04: removed
    ("m-att-replace", (), ("m-att-replace.txt",), ()),             # TC-ATT-05: same name, new bytes: a new record
])
async def test_tc_att_03_04_05_attachment_changes_follow_the_new_version(
    world: World, round1: dict, graph_provider: GraphProviderProtocol,
    key: str, kept: tuple, new: tuple, gone: tuple,
) -> None:
    """A new version copies its files with new sys_ids; a file record follows the file (name and
    bytes), so an unchanged file keeps its record and a changed one gets a new one."""
    art, old = world.art(key), round1["old_files"][key]
    old_ids = {rid for _, rid in old.values()}

    async def _settled() -> tuple[dict[str, tuple[str, str]]] | None:
        files = await files_of(graph_provider, world.connector_id, art.number)
        ok = set(files) == set(kept) | set(new) and all(files[n][1] not in old_ids for n in new)
        return (files,) if ok else None  # a tuple: no files at all is a valid answer
    try:
        (files,) = await poll(_settled, "", 300)
    except AssertionError:
        files = await files_of(graph_provider, world.connector_id, art.number)
        raise AssertionError(f"{key}: expected files {sorted(set(kept) | set(new))} (new records for {list(new)}), "
                             f"got {files}; before {old}") from None
    for name in kept:
        assert files[name][1] == old[name][1], f"{key}: the unchanged {name} got a new record"
    assert not set(gone) & set(files)


# -- order 4: revert -----------------------------------------------------------------------------------


@pytest.mark.order(4)
async def test_tc_ver_04_revert_to_an_older_version(world: World, round1: dict, pipeshub_client: PipeshubClient,
                                                    graph_provider: GraphProviderProtocol) -> None:
    s, art = world.seed, world.art("m-revert")
    first = art.sys_id
    before = await wait_present(graph_provider, world.connector_id, art.number)
    # Two syncs: a revert made before the edit is synced restores the same body, so nothing would change.
    await asyncio.to_thread(s.checkout, art, text=f"<p>{s.token('revert-v2')}</p>")
    await sync(pipeshub_client, graph_provider, world.connector_id)
    v2 = await wait_present(graph_provider, world.connector_id, art.number,
                            lambda r: r.external_revision_id != before.external_revision_id, "m-revert at version 2")
    await asyncio.to_thread(s.revert, art, first)
    await sync(pipeshub_client, graph_provider, world.connector_id)
    rec = await wait_present(graph_provider, world.connector_id, art.number,
                             lambda r: r.external_revision_id != v2.external_revision_id, "m-revert reverted")
    assert rec.id == before.id
    rec = await wait_until_record_indexing_completed(graph_provider, world.connector_id, art.number, timeout=INDEX_TIMEOUT)
    text = stream_text(pipeshub_client, world.reader.account, rec.id)
    assert art.token in text and s.token("revert-v2") not in text


# -- order 5: lifecycle -------------------------------------------------------------------------------


@pytest.mark.order(5)
async def test_tc_life_01_02_tc_del_06_disabled_connector_keeps_data_and_catches_up(
    world: World, round1: dict, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
) -> None:
    s, cid = world.seed, world.connector_id
    pipeshub_client.toggle_sync(cid, enable=False)
    count = await graph_provider.count_records(cid)
    await asyncio.to_thread(lambda: (s.article("life-new", s.kbs["i1"]), s.publish_pending(),
                                     s.rest.delete("kb_knowledge", world.art("life-del").sys_id)))
    await asyncio.sleep(30)
    assert await graph_provider.count_records(cid) == count, "records changed while the connector was off"
    assert await record(graph_provider, cid, world.art("life-del").number) is not None
    pipeshub_client.toggle_sync(cid, enable=True)
    await wait_for_sync_completion(pipeshub_client, graph_provider, cid, timeout=SYNC_TIMEOUT)
    await wait_present(graph_provider, cid, world.art("life-new").number)
    await wait_absent(graph_provider, cid, world.art("life-del").number)


@pytest.mark.order(5)
async def test_tc_life_06_reindex_one_record(world: World, pipeshub_client: PipeshubClient,
                                             graph_provider: GraphProviderProtocol) -> None:
    rec = await wait_until_record_indexing_completed(graph_provider, world.connector_id, world.art("root").number,
                                                     timeout=INDEX_TIMEOUT)
    number, title = world.art("root").number, world.art("root").title
    stamp = ((await graph_provider.get_record_by_name(world.connector_id, title)) or {}).get("lastIndexTimestamp") or 0
    pipeshub_client.reindex_record(rec.id)

    async def _reindexed() -> bool:
        now = (await graph_provider.get_record_by_name(world.connector_id, title)) or {}
        return (now.get("lastIndexTimestamp") or 0) > stamp
    # A reindex that queues nothing leaves the record as it was; the status alone stays COMPLETED.
    await poll(_reindexed, f"reindexing {number} should index it again", 300)
    rec = await wait_until_record_indexing_completed(graph_provider, world.connector_id, number, timeout=INDEX_TIMEOUT)
    assert rec.indexing_status == "COMPLETED"


@pytest.mark.order(5)
async def test_tc_life_03_05_07_second_connector_is_separate_and_deletes_clean(
    world: World, sn_oauth_config: str, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
) -> None:
    """A second connector on the same instance keeps its own records; deleting it removes them all
    (checked on exit), and re-creating it starts clean."""
    if not KB_FILTER:
        pytest.skip(NO_KB_FILTER)
    i2 = world.seed.kbs["i2"]
    expected = readable_numbers(world.seed.rest, [i2])
    main_i2 = {n: (await record(graph_provider, world.connector_id, n)).id for n in expected}
    seen_ids: list[set[str]] = []
    for attempt in range(2):
        async with servicenow_connector(pipeshub_client, graph_provider, sn_oauth_config, name=f"servicenow-it-2nd-{attempt}",
                                        filters=kb_filter([i2]), min_records=len(expected)) as second:
            rows = await graph_provider.fetch_records_by_type(second, "WEBPAGE")
            assert {r.get("externalRecordId") for r in rows} == expected
            seen_ids.append({r.get("_key") or r.get("id") for r in rows})
            assert not seen_ids[-1] & set(main_i2.values()), "the second connector shares records with the first"
    assert not seen_ids[0] & seen_ids[1], "the re-created connector reused the deleted one's records"
    for number, rid in main_i2.items():
        rec = await record(graph_provider, world.connector_id, number)
        assert rec is not None and rec.id == rid, "deleting the second connector touched the first one's records"


# -- order 6+: filters --------------------------------------------------------------------------------


@dataclass
class FilterWorld:
    seed: ServiceNowSeed
    connector_id: str
    late_cut_ms: int = 0


def _manual(on: bool, **indexing: bool) -> dict[str, Any]:
    values = {"enable_manual_sync": {"operator": "is", "type": "boolean", "value": on}}
    values.update({k: {"operator": "is", "type": "boolean", "value": v} for k, v in indexing.items()})
    return {"indexing": {"values": values}}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def fworld(world: World, sn_oauth_config: str, pipeshub_client: PipeshubClient,
                 graph_provider: GraphProviderProtocol) -> AsyncIterator[FilterWorld]:
    if not KB_FILTER:
        pytest.skip(NO_KB_FILTER)
    s = world.seed

    def build() -> None:
        uc = s.criterion("uc-f", group=s.groups["readers"])
        fa, fb = s.kb("fa", read=[uc]), s.kb("fb", read=[uc])
        c1 = s.category("f-cat-1", fa)
        s1 = s.category("f-sub-1", c1, under_category=True)
        c2 = s.category("f-cat-2", fa)
        for key, kb, cat in (("fa-c1", fa, c1), ("fa-s1", fa, s1), ("fa-c2", fa, c2), ("fa-root", fa, None), ("fb-1", fb, None)):
            s.article(key, kb, category=cat)
        s.publish_pending()

    await asyncio.to_thread(build)
    # Manual indexing: the filter steps only move records in and out, and one step syncs every KB.
    async with servicenow_connector(
        pipeshub_client, graph_provider, sn_oauth_config, name="servicenow-filter-it",
        filters=merge_filters(kb_filter([s.kbs["fa"], s.kbs["fb"]]), _manual(True)), min_records=5,
    ) as cid:
        yield FilterWorld(s, cid)


async def _apply(fw: FilterWorld, client: PipeshubClient, graph: GraphProviderProtocol, *parts: dict) -> None:
    await apply_filter_full_sync(client, graph, fw.connector_id, merge_filters(*parts, _manual(True)), timeout=SYNC_TIMEOUT)


async def _present(graph: GraphProviderProtocol, fw: FilterWorld, keys: tuple[str, ...], absent: tuple[str, ...]) -> None:
    for k in keys:
        await wait_present(graph, fw.connector_id, fw.seed.articles[k].number, what=f"{k} should be synced")
    for k in absent:
        await wait_absent(graph, fw.connector_id, fw.seed.articles[k].number, what=f"{k} should be filtered out")


@pytest.mark.order(6)
async def test_tc_filt_14_manual_indexing_syncs_without_indexing(fworld: FilterWorld,
                                                                  graph_provider: GraphProviderProtocol) -> None:
    rec = await wait_present(graph_provider, fworld.connector_id, fworld.seed.articles["fa-c1"].number)
    await poll(lambda: _status_is(graph_provider, fworld.connector_id, rec.external_record_id, "AUTO_INDEX_OFF"),
               "with manual indexing a synced article should stay AUTO_INDEX_OFF", 120)


async def _status_is(graph: GraphProviderProtocol, cid: str, external_id: str, status: str) -> bool:
    rec = await record(graph, cid, external_id)
    return rec is not None and rec.indexing_status == status


@pytest.mark.order(6)
async def test_tc_filt_03_options_are_paged_and_searchable(fworld: FilterWorld, pipeshub_client: PipeshubClient) -> None:
    s = fworld.seed
    first = pipeshub_client.get_filter_options(fworld.connector_id, "knowledge_bases", limit=1, search=s.name("f"))
    assert first.get("hasMore"), f"one of two matches per page should leave more: {first}"
    ids = {o.get("id") for o in first.get("options") or []}
    second = pipeshub_client.get_filter_options(fworld.connector_id, "knowledge_bases", limit=1, search=s.name("f"),
                                                page=2)  # the connector pages by number, with no cursor
    ids |= {o.get("id") for o in second.get("options") or []}
    assert ids == {s.kbs["fa"], s.kbs["fb"]}, ids
    if CATEGORY_FILTER:
        cats = pipeshub_client.get_filter_options(fworld.connector_id, "folder_ids", limit=50, search=s.name("f-sub-1"))
        labels = {o.get("id"): o.get("label") for o in cats.get("options") or []}
        assert labels.get(s.categories["f-sub-1"], "").startswith(s.name("fa")), labels


@pytest.mark.order(7)
async def test_tc_filt_01_04_kb_in_filter_narrows(fworld: FilterWorld, pipeshub_client: PipeshubClient,
                                                  graph_provider: GraphProviderProtocol) -> None:
    await _apply(fworld, pipeshub_client, graph_provider, kb_filter([fworld.seed.kbs["fa"]]))
    await _present(graph_provider, fworld, ("fa-c1", "fa-root"), ("fb-1",))


@pytest.mark.order(8)
async def test_tc_filt_05_kb_filter_widened_adds_back(fworld: FilterWorld, pipeshub_client: PipeshubClient,
                                                      graph_provider: GraphProviderProtocol) -> None:
    await _apply(fworld, pipeshub_client, graph_provider, kb_filter([fworld.seed.kbs["fa"], fworld.seed.kbs["fb"]]))
    await _present(graph_provider, fworld, ("fa-c1", "fb-1"), ())


@pytest.mark.order(9)
async def test_tc_filt_02_kb_not_in_filter(fworld: FilterWorld, pipeshub_client: PipeshubClient,
                                           graph_provider: GraphProviderProtocol) -> None:
    await _apply(fworld, pipeshub_client, graph_provider, kb_filter([fworld.seed.kbs["fa"]], "not_in"))
    await _present(graph_provider, fworld, ("fb-1",), ("fa-c1", "fa-root"))


@pytest.mark.order(10)
@pytest.mark.parametrize("operator,kept,dropped", [
    ("in", ("fa-c1", "fa-s1"), ("fa-c2", "fa-root")),
    ("not_in", ("fa-c2", "fa-root"), ("fa-c1", "fa-s1")),
])
async def test_category_filter_includes_or_skips_a_subtree(fworld: FilterWorld, pipeshub_client: PipeshubClient,
                                                           graph_provider: GraphProviderProtocol,
                                                           operator: str, kept: tuple, dropped: tuple) -> None:
    if not CATEGORY_FILTER:
        pytest.skip("the connector has no folder_ids (categories) filter yet")
    s = fworld.seed
    cats = {"sync": {"values": {"folder_ids": {"operator": operator, "type": "list", "value": [s.categories["f-cat-1"]]}}}}
    await _apply(fworld, pipeshub_client, graph_provider, kb_filter([s.kbs["fa"]]), cats)
    await _present(graph_provider, fworld, kept, dropped)


@pytest.mark.order(11)
async def test_tc_filt_06_07_08_date_filters(fworld: FilterWorld, pipeshub_client: PipeshubClient,
                                             graph_provider: GraphProviderProtocol) -> None:
    if not DATE_FILTERS:
        pytest.skip("the connector has no modified/created date filters yet")
    s = fworld.seed

    def late() -> int:
        last = max(_sn_ms(r["sys_updated_on"]) for r in s.rest.get(
            "kb_knowledge", f"kb_knowledge_base={s.kbs['fa']}^workflow_state=published", "sys_updated_on"))
        time.sleep(3)
        s.article("fa-late", s.kbs["fa"])
        s.publish_pending()
        row = s.rest.one("kb_knowledge", f"sys_id={s.articles['fa-late'].sys_id}", "sys_created_on")
        assert _sn_ms(row["sys_created_on"]) > last
        return (last + _sn_ms(row["sys_created_on"])) // 2

    cut = await asyncio.to_thread(late)
    scope = kb_filter([s.kbs["fa"]])

    def window(key: str, op: str, start: int | None, end: int | None) -> dict:
        return {"sync": {"values": {key: {"operator": op, "type": "datetime", "value": {"start": start, "end": end}}}}}

    await _apply(fworld, pipeshub_client, graph_provider, scope, window("modified", "is_after", cut, None))  # 06, 08
    await _present(graph_provider, fworld, ("fa-late",), ("fa-c1", "fa-root"))
    await _apply(fworld, pipeshub_client, graph_provider, scope, window("created", "is_before", None, cut))  # 07
    await _present(graph_provider, fworld, ("fa-c1", "fa-root"), ("fa-late",))


@pytest.mark.order(12)
async def test_tc_filt_09_10_11_indexing_toggles(fworld: FilterWorld, pipeshub_client: PipeshubClient,
                                                 graph_provider: GraphProviderProtocol) -> None:
    """Articles off, then attachments off: each new article (with an attachment) shows the setting
    that applied when it was synced; turning articles back on indexes the next one (TC-FILT-11)."""
    if not (INDEX_ARTICLES_FILTER and INDEX_ATTACHMENTS_FILTER):
        pytest.skip("the connector has no index-articles / index-attachments settings yet")
    s, cid = fworld.seed, fworld.connector_id
    scope = kb_filter([s.kbs["fa"]])

    def add(key: str) -> Article:
        art = s.article(key, s.kbs["fa"])
        s.attach(art, f"{key}.txt", f"{key} {s.token(key + '-file')}".encode(), "text/plain")
        s.publish_pending()
        return art

    for key, articles_on, files_on in (("idx-a", False, True), ("idx-b", True, False)):
        pipeshub_client.update_connector_filters_sync_safe(cid, filters=merge_filters(
            scope, _manual(False, knowledge_base=articles_on, attachments=files_on)))
        await wait_for_sync_completion(pipeshub_client, graph_provider, cid, timeout=SYNC_TIMEOUT)
        art = await asyncio.to_thread(add, key)
        await sync(pipeshub_client, graph_provider, cid)
        file_id, _ = await wait_file(graph_provider, cid, art.number, f"{key}.txt")
        for external_id, on in ((art.number, articles_on), (file_id, files_on)):
            await wait_present(graph_provider, cid, external_id)
            if on:
                rec = await wait_until_record_indexing_completed(graph_provider, cid, external_id, timeout=INDEX_TIMEOUT)
                assert rec.indexing_status == "COMPLETED"
            else:
                await poll(lambda e=external_id: _status_is(graph_provider, cid, e, "AUTO_INDEX_OFF"),
                           f"{key}: {external_id} should not be indexed", 180)
