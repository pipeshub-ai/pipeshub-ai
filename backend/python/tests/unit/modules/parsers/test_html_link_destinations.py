"""HTML hrefs with spaces and parentheses, from parsing to embedded text."""

import pytest

from app.models.blocks import BlocksContainer, BlockType
from app.modules.parsers.html_parser.html_to_blocks import HtmlToBlocksConverter
from tests.unit.modules.transformers.test_vectorstore_embedding_text import (
    _capturing_vectorstore,
    _index,
)

PNG = "data:image/png;base64,iVBORw0KGgo="


def _convert(body: str) -> BlocksContainer:
    return HtmlToBlocksConverter().convert(f"<html><body>{body}</body></html>")


async def _embedded(container: BlocksContainer) -> list[str]:
    vs, capture = _capturing_vectorstore()
    await _index(vs, container)
    return capture.dense


def _texts(container: BlocksContainer) -> list[str]:
    return [b.data.strip() for b in container.blocks if b.type == BlockType.TEXT and b.data]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("anchor", "stored_href", "embedded"),
    [
        (
            '<a href="https://en.wikipedia.org/wiki/New York">New York</a> is big',
            "https://en.wikipedia.org/wiki/New%20York",
            "New York is big",
        ),
        (
            '<a href="https://en.wikipedia.org/wiki/New York (state)#History">History</a> next',
            "https://en.wikipedia.org/wiki/New%20York%20%28state%29#History",
            "History next",
        ),
        (
            '<a href="https://example.com/docs/Report (final).pdf">Report</a> ok',
            "https://example.com/docs/Report%20%28final%29.pdf",
            "Report ok",
        ),
        (
            '<a href="https://example.com/docs/Report (Q3 (final)).pdf">Report</a> ok',
            "https://example.com/docs/Report%20%28Q3%20%28final%29%29.pdf",
            "Report ok",
        ),
        (
            '<a href="https://en.wikipedia.org/wiki/New York (borough (Manhattan))#History">'
            "History</a> next",
            "https://en.wikipedia.org/wiki/New%20York%20%28borough%20%28Manhattan%29%29#History",
            "History next",
        ),
        (
            '<a href="https://example.com/wiki/New York (borough (Manhattan))">Page</a>',
            "https://example.com/wiki/New%20York%20%28borough%20%28Manhattan%29%29",
            "Page",
        ),
        (
            '<a href="https://en.wikipedia.org/wiki/New&nbsp;York&nbsp;(state)#History">History</a> next',
            "https://en.wikipedia.org/wiki/New%C2%A0York%C2%A0%28state%29#History",
            "History next",
        ),
        (
            '<a href="https://en.wikipedia.org/wiki/New York">https://en.wikipedia.org/wiki/New York</a>'
            " is big",
            "https://en.wikipedia.org/wiki/New%20York",
            "en.wikipedia.org is big",
        ),
        (
            '<a href="https://example.com/docs/Report (final).pdf">'
            "https://example.com/docs/Report (final).pdf</a>",
            "https://example.com/docs/Report%20%28final%29.pdf",
            "example.com",
        ),
        (
            '<a href="https://example.com/guide">https://example.com/guide for details</a>',
            "https://example.com/guide",
            "example.com for details",
        ),
        (
            '<a href="https://example.com/docs">https://example.com/docs (opens in a new tab)</a>',
            "https://example.com/docs",
            "example.com (opens in a new tab)",
        ),
        (
            '<a href="https://example.com/docs/Acme Corp" title="Acme">Acme Corp</a> ok',
            '(https://example.com/docs/Acme%20Corp "Acme")',
            "Acme Corp ok",
        ),
    ],
)
async def test_href_is_stored_encoded_and_embedded_as_anchor_text(
    anchor: str, stored_href: str, embedded: str
) -> None:
    container = _convert(f"<p>{anchor}</p>")

    [stored] = _texts(container)
    assert stored_href in stored
    assert await _embedded(container) == [embedded]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("href", "encoded"),
    [
        ("https://example.com/a b", "https://example.com/a%20b"),
        (
            "https://example.com/docs/New York (HQ).pdf",
            "https://example.com/docs/New%20York%20%28HQ%29.pdf",
        ),
        (
            "https://example.com/docs/Report (Q3 (final)).pdf",
            "https://example.com/docs/Report%20%28Q3%20%28final%29%29.pdf",
        ),
        (
            "https://example.com/docs/New&nbsp;York&nbsp;(HQ).pdf",
            "https://example.com/docs/New%C2%A0York%C2%A0%28HQ%29.pdf",
        ),
    ],
)
async def test_linked_image_leaves_no_bracket_fragments(href: str, encoded: str) -> None:
    container = _convert(
        f'<p>Carol <a href="{href}"><img alt="Profile" src="{PNG}"></a> joined '
        f'<a href="{href}">the team</a></p>'
    )

    assert _texts(container) == ["Carol", f"joined [the team]({encoded})"]
    assert [b.type for b in container.blocks].count(BlockType.IMAGE) == 1
    assert [text.strip() for text in await _embedded(container)] == [
        "Carol", "joined the team", "Profile",
    ]


def test_inline_data_uri_source_is_not_encoded() -> None:
    svg = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    container = _convert(f'<p><img alt="Logo" src="{svg}"></p>')

    image = next(b for b in container.blocks if b.type == BlockType.IMAGE)
    assert image.data["uri"] == svg
