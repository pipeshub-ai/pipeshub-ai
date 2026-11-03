"""An incremental sync, as a scheduled run makes it, on server-rendered and script-rendered sites.

A page whose text changed is re-indexed in place, a page newly linked is stored and sent for
indexing, and a page that didn't change is left alone, whether plain HTTP or the browser reads it.
A scheduled run calls the same ``run_sync`` as these tests.
"""

import pytest
from web_behaviour_fakes import (
    START_URL,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
    html_page,
)

from app.config.constants.arangodb import ProgressStatus

GUIDE = "http://site.test/guide"
ABOUT = "http://site.test/about"
NEW = "http://site.test/new-page"
LONG = "Words that make this a real page and not an empty shell. " * 30
SHELL = b"<html><head><title>App</title></head><body><div id='root'></div><script src='/app.js'></script></body></html>"


def _page(site: FakeWeb, mode: str, url: str, title: str, *links: str, text: str) -> None:
    if mode == "csr":
        # The server sends an empty shell; only the browser sees the content.
        site.add(url, Page(body=SHELL, rendered=html_page(title, *links, text=text), pre_render_text_len=0))
    else:
        site.html(url, title, *links, text=text)


def _site(site: FakeWeb, mode: str, *, guide: str, new_page: bool) -> None:
    links = ["/guide", "/about"] + (["/new-page"] if new_page else [])
    _page(site, mode, START_URL, "Home", *links, text=LONG)
    _page(site, mode, GUIDE, "Guide", text=guide + LONG)
    _page(site, mode, ABOUT, "About", text="About us. " + LONG)
    if new_page:
        _page(site, mode, NEW, "New page", text="Release notes for 2.0. " + LONG)


@pytest.mark.parametrize("mode", ["ssr", "csr", "always_browser"])
async def test_a_changed_page_is_re_indexed_and_a_new_page_indexed_on_the_next_sync(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector, mode: str,
) -> None:
    _site(browser, mode, guide="Install with pip. ", new_page=False)
    connector = await make_connector(use_headless_browser=mode == "always_browser")
    await connector.run_sync()
    assert connector.use_headless_browser is (mode != "ssr"), "the site was read the way it renders"
    db.mark_indexed()
    before = {url: r.model_copy() for url, r in db.pages().items()}
    db.new_batches.clear()
    db.content_updates.clear()
    db.metadata_updates.clear()

    _site(browser, mode, guide="Install with uv. ", new_page=True)
    await connector.run_sync()

    pages = db.pages()
    # The changed page: same record, new version and revision, its new text stored, sent to be re-indexed.
    guide = pages[GUIDE]
    assert GUIDE in [r.weburl for r in db.content_updates]
    assert guide.id == before[GUIDE].id and guide.version == before[GUIDE].version + 1
    assert guide.external_revision_id != before[GUIDE].external_revision_id
    assert b"Install with uv" in browser.storage_docs[guide.storage_document_id]
    # The new page: stored, and waiting to be indexed.
    assert NEW in pages
    assert [r.weburl for batch in db.new_batches for r in batch] == [NEW]
    assert pages[NEW].indexing_status == ProgressStatus.QUEUED.value
    # The unchanged page: not rewritten, not re-indexed.
    about = pages[ABOUT]
    assert (about.version, about.external_revision_id) == (before[ABOUT].version, before[ABOUT].external_revision_id)
    assert about.indexing_status == ProgressStatus.COMPLETED.value
    assert ABOUT not in [r.weburl for r in db.content_updates + db.metadata_updates]
