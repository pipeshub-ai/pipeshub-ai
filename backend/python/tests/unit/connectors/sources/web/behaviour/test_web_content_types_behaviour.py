"""What the crawler stores for each kind of response: HTML pages, documents,
oversized downloads, and pages the user's filters exclude.
"""

import base64

import pytest
from web_behaviour_fakes import (
    START_URL,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
    html_page,
)

from app.config.constants.arangodb import MimeTypes, ProgressStatus

MB = 1024 * 1024
TOO_LARGE = (
    "This file is larger than this connector's 1 MB size limit, so it wasn't downloaded. "
    "Raise the Maximum Size in MB setting to include it, then sync again."
)
OFFICE_TYPES = "application/vnd.openxmlformats-officedocument"


def site_bytes(site: FakeWeb, db: FakeRecordsDb, url: str) -> bytes:
    return site.storage_docs[db.pages()[url].storage_document_id]


def _extensions(operator: str, *extensions: str) -> dict:
    return {"sync": {"values": {"file_extensions": {"operator": operator, "value": list(extensions), "type": "multiselect"}}}}


@pytest.mark.parametrize(
    ("path", "content_type", "mime", "extension"),
    [
        ("/files/report.pdf", "application/pdf", MimeTypes.PDF, "pdf"),
        ("/files/report.pdf", "application/octet-stream", MimeTypes.PDF, "pdf"),
        ("/download?id=7", "application/pdf", MimeTypes.PDF, "pdf"),
        ("/files/notes.txt", "text/plain; charset=utf-8", MimeTypes.PLAIN_TEXT, "txt"),
        ("/files/letter.docx", f"{OFFICE_TYPES}.wordprocessingml.document", MimeTypes.DOCX, "docx"),
        ("/files/budget.xlsx", f"{OFFICE_TYPES}.spreadsheetml.sheet", MimeTypes.XLSX, "xlsx"),
        ("/files/deck.pptx", f"{OFFICE_TYPES}.presentationml.presentation", MimeTypes.PPTX, "pptx"),
        ("/files/old.doc", "application/msword", MimeTypes.DOC, "doc"),
    ],
)
async def test_linked_documents_are_stored_with_their_real_type_and_bytes(
    path: str, content_type: str, mime: MimeTypes, extension: str,
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    url = f"http://site.test{path}"
    payload = b"PK\x03\x04 binary document bytes \x00\x01"
    site.html(START_URL, "Home", path)
    site.add(url, Page(body=payload, content_type=content_type))

    await (await make_connector()).run_sync()

    record = db.pages()[url]
    assert (record.mime_type, record.extension) == (mime.value, extension)
    assert site.storage_docs[record.storage_document_id] == payload


async def test_a_stored_html_page_keeps_its_text_and_drops_scripts_and_navigation(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.add(START_URL, Page(body=html_page("Home", text="Welcome to the handbook").replace(
        b"</body>", b"<nav>Menu links</nav><script>track()</script></body>")))

    await (await make_connector(crawl_type="single")).run_sync()

    stored = site.storage_docs[db.pages()[START_URL].storage_document_id]
    assert b"Welcome to the handbook" in stored
    assert b"track()" not in stored and b"Menu links" not in stored


async def test_page_images_are_embedded_so_the_stored_copy_is_self_contained(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    site.add(START_URL, Page(body=html_page("Home", body='<img src="/pic.png">')))
    site.add("http://site.test/pic.png", Page(body=png, content_type="image/png"))

    await (await make_connector(crawl_type="single")).run_sync()

    stored = site.storage_docs[db.pages()[START_URL].storage_document_id]
    assert b'src="data:image/png;base64,' in stored


async def test_with_image_indexing_off_images_are_dropped_not_downloaded(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.add(START_URL, Page(body=html_page("Home", body='<img src="/pic.png">')))
    filters = {"indexing": {"values": {"images": {"operator": "is", "value": False, "type": "boolean"}}}}

    await (await make_connector(crawl_type="single", filters=filters)).run_sync()

    stored = site.storage_docs[db.pages()[START_URL].storage_document_id]
    assert b"<img" not in stored
    assert site.gets("http://site.test/pic.png") == 0


async def test_with_webpage_indexing_off_pages_are_stored_but_not_queued_for_indexing(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/guide.pdf")
    site.add("http://site.test/guide.pdf", Page(body=b"%PDF-1.4", content_type="application/pdf"))
    filters = {"indexing": {"values": {"webpages": {"operator": "is", "value": False, "type": "boolean"}}}}

    await (await make_connector(filters=filters)).run_sync()

    assert db.pages()[START_URL].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
    assert db.pages()["http://site.test/guide.pdf"].indexing_status != ProgressStatus.AUTO_INDEX_OFF.value


async def test_an_oversized_download_is_shown_as_too_large_without_fetching_its_body(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    big = "http://site.test/huge.pdf"
    site.html(START_URL, "Home", "/huge.pdf", "/small")
    site.add(big, Page(body=b"x" * (2 * MB), content_type="application/pdf"))
    site.html("http://site.test/small", "Small")

    await (await make_connector(max_size_mb=1)).run_sync()

    assert site.gets(big) == 0
    assert db.pages()[big].reason == TOO_LARGE
    assert "http://site.test/small" in db.pages()


async def test_an_oversized_download_does_not_start_the_headless_browser(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    big = "http://site.test/huge.pdf"
    site.html(START_URL, "Home", "/huge.pdf")
    site.add(big, Page(body=b"x" * (2 * MB), content_type="application/pdf"))
    connector = await make_connector(max_size_mb=1)
    starts_after_init = site.browser_starts

    await connector.run_sync()

    assert big not in site.browser_visits
    assert site.browser_starts == starts_after_init


async def test_an_oversized_page_without_a_declared_size_is_shown_as_too_large(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    big = "http://site.test/stream.pdf"
    site.html(START_URL, "Home", "/stream.pdf")
    site.add(big, Page(body=b"x" * (2 * MB), content_type="application/pdf", chunked=True))

    await (await make_connector(max_size_mb=1)).run_sync()

    assert db.pages()[big].indexing_status == ProgressStatus.FAILED.value
    assert db.pages()[big].reason == TOO_LARGE


async def test_a_page_that_grew_too_big_keeps_its_stored_record(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    doc = "http://site.test/manual.pdf"
    site.html(START_URL, "Home", "/manual.pdf")
    site.add(doc, Page(body=b"%PDF small", content_type="application/pdf"))
    connector = await make_connector(max_size_mb=1)
    await connector.run_sync()
    first = db.pages()[doc]

    site.add(doc, Page(body=b"x" * (2 * MB), content_type="application/pdf"))
    await connector.run_sync()

    assert db.pages()[doc].external_revision_id == first.external_revision_id
    assert db.deleted == []


async def test_an_only_pdfs_filter_still_finds_pdfs_linked_from_pages(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/docs/")
    site.html("http://site.test/docs/", "Docs", "/docs/manual.pdf")
    site.add("http://site.test/docs/manual.pdf", Page(body=b"%PDF-1.4 manual", content_type="application/pdf"))

    await (await make_connector(filters=_extensions("in", "pdf"))).run_sync()

    assert set(db.pages()) == {"http://site.test/docs/manual.pdf"}


async def test_an_exclude_pdfs_filter_skips_pdfs_and_keeps_pages(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/manual.pdf", "/about")
    site.add("http://site.test/manual.pdf", Page(body=b"%PDF-1.4 manual", content_type="application/pdf"))
    site.html("http://site.test/about", "About")

    await (await make_connector(filters=_extensions("not_in", "pdf"))).run_sync()

    assert set(db.pages()) == {START_URL, "http://site.test/about"}


async def test_a_single_page_crawl_honours_the_extension_filter(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home")

    await (await make_connector(crawl_type="single", filters=_extensions("in", "pdf"))).run_sync()

    assert db.pages() == {}


async def test_robust_mode_only_pdfs_filter_still_finds_linked_pdfs(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/manual.pdf")
    browser.add("http://site.test/manual.pdf", Page(body=b"%PDF-1.4 manual", content_type="application/pdf"))

    await (await make_connector(use_headless_browser=True, filters=_extensions("in", "pdf"))).run_sync()

    assert set(db.pages()) == {"http://site.test/manual.pdf"}
    assert site_bytes(browser, db, "http://site.test/manual.pdf").startswith(b"%PDF-")


@pytest.mark.parametrize(
    ("path", "content_type", "payload"),
    [
        ("/manual.pdf", "application/pdf", b"%PDF-1.4 manual"),
        ("/letter.docx", f"{OFFICE_TYPES}.wordprocessingml.document", b"PK\x03\x04 docx"),
    ],
)
async def test_robust_mode_stores_linked_documents_as_the_real_file(
    path: str, content_type: str, payload: bytes,
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    url = f"http://site.test{path}"
    browser.html(START_URL, "Home", path)
    browser.add(url, Page(body=payload, content_type=content_type))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert site_bytes(browser, db, url) == payload
    assert url not in browser.browser_visits


async def test_a_blocked_document_is_not_retried_in_the_browser(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/manual.pdf")
    browser.add("http://site.test/manual.pdf", Page(status=403, body=b"blocked", rendered_status=200))

    await (await make_connector()).run_sync()

    assert "http://site.test/manual.pdf" not in browser.browser_visits
    assert "403 Forbidden" in (db.pages()["http://site.test/manual.pdf"].reason or "")


async def test_robust_mode_fetches_the_file_a_redirect_lands_on(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    pdf = "http://site.test/handbook.pdf"
    browser.html(START_URL, "Home", "/handbook", "/handbook.pdf")
    browser.redirect("http://site.test/handbook", "/handbook.pdf")
    browser.add(pdf, Page(body=b"%PDF-1.4 handbook", content_type="application/pdf"))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert site_bytes(browser, db, pdf).startswith(b"%PDF-")
    assert db.pages()[pdf].mime_type == MimeTypes.PDF.value
    stored = [browser.storage_docs[d] for d in browser.storage_uploads]
    assert stored.count(b"%PDF-1.4 handbook") == 1
    assert not any(b"<embed" in doc for doc in stored)


async def test_robust_mode_single_page_that_redirects_to_a_file_stores_the_file(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    pdf = "http://site.test/handbook.pdf"
    browser.redirect("http://site.test/handbook", "/handbook.pdf")
    browser.add(pdf, Page(body=b"%PDF-1.4 handbook", content_type="application/pdf"))

    await (await make_connector("http://site.test/handbook", crawl_type="single", use_headless_browser=True)).run_sync()

    assert site_bytes(browser, db, pdf) == b"%PDF-1.4 handbook"


async def test_robust_mode_never_stores_the_viewer_page_when_the_file_is_blocked(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/handbook")
    browser.redirect("http://site.test/handbook", "/handbook.pdf")
    browser.add("http://site.test/handbook.pdf",
                Page(status=403, rendered_status=200, body=b"blocked", content_type="application/pdf"))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert not any(b"<embed" in doc for doc in browser.storage_docs.values())
    assert "http://site.test/handbook.pdf" not in db.pages()
    assert "403 Forbidden" in (db.pages()["http://site.test/handbook"].reason or "")


async def test_an_svg_is_stored_as_an_svg_not_as_xml(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    diagram = "http://site.test/diagram"
    site.add(diagram, Page(body=b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', content_type="image/svg+xml"))

    await (await make_connector(diagram, crawl_type="single")).run_sync()

    assert (db.pages()[diagram].mime_type, db.pages()[diagram].extension) == (MimeTypes.SVG.value, "svg")


@pytest.mark.parametrize("aborts", [False, True], ids=["shown-in-viewer", "navigation-aborted"])
async def test_robust_mode_fetches_a_document_served_from_a_url_without_an_extension(
    aborts: bool, browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    url = "http://site.test/download?id=7"
    browser.html(START_URL, "Home", "/download?id=7")
    browser.add(url, Page(body=b"%PDF-1.4 report", content_type="application/pdf", browser_aborts=aborts))

    await (await make_connector(use_headless_browser=True)).run_sync()

    record = db.pages()[url]
    assert record.mime_type == MimeTypes.PDF.value
    assert site_bytes(browser, db, url) == b"%PDF-1.4 report"
