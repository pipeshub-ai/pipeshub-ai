"""The HTML clean-up shared by the Confluence and Jira Cloud connectors.

These helpers decide what reaches the index when a page or issue is streamed:
images behind a login must be inlined (or dropped) rather than left as links
nobody can open, and nothing from another host may be fetched with our token.
"""

import base64
import logging

from bs4 import BeautifulSoup

from app.connectors.sources.atlassian.core.confluence_html import (
    HtmlImageContext,
    bytes_to_data_uri,
    extract_attachment_filename_from_download_url,
    extract_content_base_url,
    inline_authenticated_images_in_html,
    is_cloud_attachment_download_url,
    is_same_origin,
    prepare_streaming_html,
    prepend_title_to_html,
    resolve_relative_urls_in_html,
)
from app.connectors.sources.atlassian.core.html_utils import (
    extract_attachment_ids,
    inline_images_as_base64,
)

BASE = "https://acme.atlassian.net/wiki"
PNG = b"\x89PNG\r\n\x1a\nfake"


class RecordingDownloader:
    """Answers image downloads by URL and remembers which URLs were requested."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.requested: list[tuple[str, HtmlImageContext]] = []

    async def __call__(self, url: str, context: HtmlImageContext) -> object:
        self.requested.append((url, context))
        answer = self.answers.get(url)
        if isinstance(answer, Exception):
            raise answer
        return answer


def img_srcs(html: str) -> list[str]:
    return [img.get("src") for img in BeautifulSoup(html, "html.parser").find_all("img")]


class TestConfluenceStreamingHtml:
    async def test_same_origin_images_are_inlined_and_foreign_ones_never_fetched(self) -> None:
        html = (
            '<p><img src="/wiki/download/attachments/1/chart.png" alt="Chart" '
            'data-linked-resource-id="att9" data-linked-resource-type="attachment" '
            'data-media-id=" m-1 " data-linked-resource-default-alias="chart.png"></p>'
            '<p><img src="https://evil.example.com/track.png"></p>'
            '<p><img src="//evil.example.com/protocol-relative.png"></p>'
            '<p><img src="data:image/png;base64,AAAA"></p>'
            "<p><img></p>"
        )
        download = RecordingDownloader({"https://acme.atlassian.net/wiki/download/attachments/1/chart.png": (PNG, "image/png")})

        out = await prepare_streaming_html(html, {"_links": {"base": BASE}, "title": "Q3 <plan>"}, download)

        assert [url for url, _ in download.requested] == ["https://acme.atlassian.net/wiki/download/attachments/1/chart.png"]
        context = download.requested[0][1]
        assert context == HtmlImageContext(
            alt_text="Chart", linked_resource_id="att9", linked_resource_type="attachment",
            media_id="m-1", default_alias="chart.png",
        )
        srcs = img_srcs(out)
        assert srcs[0] == "data:image/png;base64," + base64.b64encode(PNG).decode()
        assert srcs[1] == "https://evil.example.com/track.png"
        assert srcs[2] == "https://evil.example.com/protocol-relative.png"
        assert srcs[3] == "data:image/png;base64,AAAA"
        assert out.startswith("<h1>Q3 &lt;plan&gt;</h1>")

    async def test_a_failed_or_declined_download_leaves_the_page_intact(self) -> None:
        html = (
            '<img src="/wiki/download/attachments/1/a.png">'
            '<img src="/wiki/download/attachments/1/b.png">'
            '<img src="/wiki/download/thumbnails/1/c.pdf">'
        )
        download = RecordingDownloader({
            "https://acme.atlassian.net/wiki/download/attachments/1/a.png": RuntimeError("timeout"),
            "https://acme.atlassian.net/wiki/download/attachments/1/b.png": (b"<html>login</html>", "text/html"),
            "https://acme.atlassian.net/wiki/download/thumbnails/1/c.pdf": None,
        })

        out = await inline_authenticated_images_in_html(html, BASE, download, logger=logging.getLogger("t"))

        assert len(download.requested) == 3
        assert img_srcs(out) == [
            "/wiki/download/attachments/1/a.png",
            "/wiki/download/attachments/1/b.png",
            "/wiki/download/thumbnails/1/c.pdf",
        ]

    async def test_without_a_base_url_nothing_is_fetched_but_the_title_is_still_added(self) -> None:
        download = RecordingDownloader({})
        out = await prepare_streaming_html('<img src="/x.png">', {"title": "Doc"}, download, title="Comment by Ana")
        assert download.requested == []
        assert out == '<h1>Comment by Ana</h1>\n<img src="/x.png">'

    def test_relative_links_become_absolute_and_other_urls_are_kept(self) -> None:
        html = (
            '<a href="/wiki/spaces/ENG">space</a><a href="#top">top</a><a href="mailto:a@b.c">mail</a>'
            '<img srcset="/wiki/a.png 1x, , /wiki/b.png 2x" src="">'
            '<form action="submit"></form><div class="note" title="/not-a-url"></div>'
        )
        out = BeautifulSoup(resolve_relative_urls_in_html(html, BASE), "html.parser")
        links = [a["href"] for a in out.find_all("a")]
        assert links == ["https://acme.atlassian.net/wiki/spaces/ENG", "#top", "mailto:a@b.c"]
        assert out.img["srcset"] == "https://acme.atlassian.net/wiki/a.png 1x, https://acme.atlassian.net/wiki/b.png 2x"
        assert out.form["action"] == "https://acme.atlassian.net/submit"
        assert out.div["title"] == "/not-a-url"
        assert resolve_relative_urls_in_html("", BASE) == ""

    def test_base_url_falls_back_to_the_self_link(self) -> None:
        assert extract_content_base_url({"_links": {"self": "https://dc.example.com/rest/api/content/1"}}) == "https://dc.example.com"
        assert extract_content_base_url({"_links": {"self": 42}}) is None
        assert extract_content_base_url({}) is None

    def test_origin_check_rejects_other_hosts_schemes_and_junk(self) -> None:
        assert is_same_origin("/wiki/x.png", BASE)
        assert not is_same_origin("http://acme.atlassian.net/wiki/x.png", BASE)
        assert not is_same_origin("https://acme.atlassian.net.evil.com/x.png", BASE)
        assert not is_same_origin("", BASE)
        assert not is_same_origin("http://[::1", BASE), "an unparseable URL is treated as foreign"

    def test_attachment_download_urls_are_recognised(self) -> None:
        assert extract_attachment_filename_from_download_url("/wiki/download/attachments/12/My%20Pic.png?version=1") == "My Pic.png"
        assert is_cloud_attachment_download_url("https://acme.atlassian.net/wiki/download/thumbnails/12/doc.pdf")
        assert extract_attachment_filename_from_download_url("/wiki/images/icons/emoticons/smile.svg") is None
        assert extract_attachment_filename_from_download_url("   ") is None

    def test_image_bytes_get_a_usable_data_uri_type(self) -> None:
        assert bytes_to_data_uri(b"x", "application/pdf") is None
        assert bytes_to_data_uri(b"<svg/>", "image/svg+xml").startswith("data:image/svg+xml;base64,")
        assert bytes_to_data_uri(b"x", "image/jpeg").startswith("data:image/jpeg;base64,")
        assert bytes_to_data_uri(b"x", "image/gif", "https://h/download/attachments/1/anim.GIF?v=2").startswith("data:image/gif;base64,")
        assert bytes_to_data_uri(b"x", "image/gif", "https://h/download/attachments/1/anim").startswith("data:image/png;base64,")

    def test_title_goes_inside_the_body_of_a_full_document(self) -> None:
        out = prepend_title_to_html("<html><body><p>Hi</p></body></html>", "  Roadmap & goals ")
        assert "<body><h1>Roadmap &amp; goals</h1><p>Hi</p></body>" in out
        assert prepend_title_to_html("<p>x</p>", "   ") == "<p>x</p>"


class TestJiraRenderedHtml:
    async def test_attachment_images_are_inlined_unwrapped_or_reduced_to_alt_text(self) -> None:
        html = (
            '<p><span class="image-wrap"><a href="/rest/api/3/attachment/content/100">'
            '<img src="/rest/api/3/attachment/content/100" alt="shot.png"></a></span></p>'
            '<p><img src="/rest/api/3/attachment/thumbnail/200" alt="too-big.png"></p>'
            '<p><img src="/rest/api/3/attachment/content/300" alt="notes.pdf"></p>'
            '<p><img src="/images/icons/emoticons/smile.png" class="emoticon"></p>'
            '<p><img src="/images/icons/link.png" class="rendericon"></p>'
            '<p><span class="nobr"><a href="/secure/attachment/400/spec.docx">spec.docx</a><sup></sup></span></p>'
            '<p><a href="https://acme.atlassian.net/secure/ViewProfile.jspa?accountId=abc">Ana Lima</a>'
            '<a href="/jira/people/xyz"></a></p>'
        )
        attachments = {"100": {"id": "100"}, "200": {"id": "200"}, "300": {"id": "300"}}
        fetched: list[str] = []

        async def fetch_base64(att: dict) -> str | None:
            fetched.append(att["id"])
            return "data:image/png;base64,QUJD" if att["id"] == "100" else None

        out, inlined = await inline_images_as_base64(html, attachments, lambda i: i in {"100", "200"}, fetch_base64)
        soup = BeautifulSoup(out, "html.parser")

        assert inlined == {"100"}
        assert fetched == ["100", "200"], "a non-image attachment is never downloaded"
        first = soup.find_all("p")[0]
        assert first.img.parent.name == "p", "wrappers around an inlined image are removed"
        assert first.img["src"] == "data:image/png;base64,QUJD"
        assert "too-big.png" in soup.get_text() and "notes.pdf" in soup.get_text()
        assert [i["src"] for i in soup.find_all("img")] == ["data:image/png;base64,QUJD", "/images/icons/emoticons/smile.png"]
        assert "spec.docx" in soup.get_text() and not soup.find("sup") and not soup.find("span", class_="nobr")
        assert "@Ana Lima" in soup.get_text()
        assert "/secure/attachment" not in out and "ViewProfile" not in out

    async def test_empty_html_is_returned_unchanged(self) -> None:
        async def never(_: dict) -> str | None:
            raise AssertionError("must not fetch")

        assert await inline_images_as_base64("", {}, lambda _: True, never) == ("", set())

    def test_attachment_ids_are_found_in_cloud_and_data_center_urls(self) -> None:
        html = (
            '<a href="/rest/api/3/attachment/content/11">a</a>'
            '<img src="/rest/api/2/attachment/thumbnail/22">'
            '<table><tr><td><img src="/secure/thumbnail/33/x.png"></td></tr></table>'
            '<a href="/secure/attachment/44/y.pdf">y</a><a href="/browse/ABC-1">issue</a>'
        )
        assert extract_attachment_ids(html) == {"11", "22", "33", "44"}
        assert extract_attachment_ids("") == set()
