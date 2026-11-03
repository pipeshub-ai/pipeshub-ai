"""What text a page is indexed with (scenarios RX-07 to RX-10; audit FETCH-21, FETCH-23)."""

from bs4 import BeautifulSoup
from web_behaviour_fakes import MakeConnector

from app.connectors.sources.web.crawl4ai_fetcher import FetchResult

ARTICLE = "Configure the survey campaign by choosing a channel and a template. " * 6


def _text(connector, html: str) -> str:
    _, text = connector._page_title_and_text(html.encode(), "http://site.test/page")
    return text.decode()


async def test_content_inside_an_aspnet_form_is_kept(make_connector: MakeConnector) -> None:
    connector = await make_connector()
    html = f"<html><body><form id='aspnetForm'><input name='x'><p>{ARTICLE}</p><button>Go</button></form></body></html>"

    text = _text(connector, html)

    assert "Configure the survey campaign" in text
    assert "Go" not in text


async def test_site_chrome_and_cookie_banners_go_but_an_articles_own_header_stays(make_connector: MakeConnector) -> None:
    connector = await make_connector()
    html = (
        "<html><body><header>Site header Sign in</header><aside>Related links</aside>"
        "<div id='onetrust-consent-sdk'>We use cookies</div>"
        f"<article><header><h1>Survey campaigns</h1></header><p>{ARTICLE}</p><footer>Was this helpful?</footer></article>"
        "<footer>Copyright</footer></body></html>"
    )

    text = _text(connector, html)

    assert "Survey campaigns" in text and "Was this helpful?" in text
    for chrome in ("Site header", "Related links", "We use cookies", "Copyright"):
        assert chrome not in text


async def test_only_the_main_content_is_kept_when_the_page_marks_it(make_connector: MakeConnector) -> None:
    connector = await make_connector()
    html = f"<html><body><div class='megamenu'>Products Pricing Support</div><main><p>{ARTICLE}</p></main><div>Newsletter signup</div></body></html>"

    text = _text(connector, html)

    assert "Configure the survey campaign" in text
    assert "Newsletter" not in text and "Pricing" not in text


async def test_a_tiny_main_does_not_hide_the_rest_of_the_page(make_connector: MakeConnector) -> None:
    connector = await make_connector()
    html = f"<html><body><main><div id='app-widget'>Loading</div></main><div class='content'><p>{ARTICLE}</p></div></body></html>"

    assert "Configure the survey campaign" in _text(connector, html)


async def test_lazy_images_get_their_real_address(make_connector: MakeConnector) -> None:
    connector = await make_connector()
    soup = BeautifulSoup(
        "<img src='data:image/gif;base64,R0lGOD' data-src='/img/a.png'><img data-lazy-src='/img/b.png'><img src='/img/c.png' data-src='/x.png'>",
        "html.parser",
    )

    connector._resolve_lazy_images(soup)

    assert [img["src"] for img in soup.find_all("img")] == ["/img/a.png", "/img/b.png", "/img/c.png"]


async def test_a_rendered_non_utf8_page_is_not_decoded_twice(make_connector: MakeConnector) -> None:
    connector = await make_connector()
    rendered = FetchResult(
        url="http://site.test/page", success=True, status_code=200, content_type="text/html",
        html="<html><head><meta charset='windows-1252'></head><body><p>Café crème — naïve</p></body></html>",
    )

    response = connector._crawl4ai_result_to_response(rendered, "http://site.test/page")
    _, text = connector._page_title_and_text(response.content_bytes, response.final_url)

    assert "Café crème — naïve" in text.decode()


async def test_a_rendered_page_keeps_its_robots_and_validator_headers(make_connector: MakeConnector) -> None:
    """A page rendered in the browser used to lose X-Robots-Tag, so a noindex page was stored."""
    connector = await make_connector()
    rendered = FetchResult(
        url="http://site.test/page", success=True, status_code=200, content_type="text/html",
        html="<html><body><p>Private notes</p></body></html>",
        headers={"X-Robots-Tag": "noindex, nofollow", "ETag": '"v2"', "Last-Modified": "Mon, 12 Oct 2026 10:00:00 GMT"},
    )

    response = connector._crawl4ai_result_to_response(rendered, "http://site.test/page")

    assert connector._page_robots_directives(response) == {"noindex", "nofollow"}
    assert connector._header(response.headers, "ETag") == '"v2"'
