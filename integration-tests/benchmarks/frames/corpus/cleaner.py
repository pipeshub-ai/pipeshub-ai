"""Turning MediaWiki parser output into the article HTML PipesHub ingests.

Kept: title, headings, paragraphs, lists, infoboxes, wikitables and
inter-article links (rewritten to canonical URLs so every system sees the
same link graph). Stripped: images and figures (PipesHub would fetch and
base64-inline them), navigation boxes, edit links, reference markers and the
References / External links style sections. A deterministic plain-text view
with tables flattened to `cell | cell` rows feeds BM25, the oracle and the
citation fuzzy-match.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

from benchmarks.frames.dataset.urls import canonical_article_url

_STRIP_SELECTORS = (
    "style", "script", "link", "meta", "img", "figure", "picture", "audio", "video",
    ".thumb", ".gallery", ".navbox", ".navbox-styles", ".vertical-navbox", ".sidebar",
    ".mw-editsection", "sup.reference", ".reflist", ".references", ".hatnote", ".ambox",
    ".metadata", ".noprint", ".mw-empty-elt", ".sistersitebox", ".mw-authority-control",
    "#toc", ".toc", ".mw-jump-link",
)
_DROP_SECTIONS = frozenset({
    "references", "external links", "notes", "further reading", "bibliography",
    "sources", "citations", "footnotes", "notes and references", "works cited",
})
_HEADING_TAGS = ("h2", "h3", "h4", "h5", "h6")
_KEEP_ATTRIBUTES = frozenset({"href", "class", "colspan", "rowspan", "scope"})
_NON_ARTICLE_NAMESPACES = frozenset({
    "file", "image", "category", "help", "wikipedia", "template", "portal", "special",
    "talk", "user", "module", "draft", "mediawiki", "book", "timedtext", "wp",
})
_BLANK_LINES = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[ \t ]+")
_BLOCK_TAGS = (
    "p", "li", "dt", "dd", "div", "section", "blockquote", "pre", "caption",
    "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "dl",
)


@dataclass(frozen=True)
class CleanedArticle:
    html: str
    text: str
    outlinks: tuple[str, ...]


def _heading_level(node: object) -> int | None:
    if not isinstance(node, Tag):
        return None
    if node.name in _HEADING_TAGS:
        return int(node.name[1])
    if "mw-heading" in (node.get("class") or []):
        inner = node.find(_HEADING_TAGS)
        return int(inner.name[1]) if isinstance(inner, Tag) else None
    return None


def _drop_sections(root: Tag) -> None:
    for heading in list(root.find_all(_HEADING_TAGS)):
        if heading.parent is None or heading.get_text(" ", strip=True).lower() not in _DROP_SECTIONS:
            continue
        wrapper = heading.parent if "mw-heading" in (heading.parent.get("class") or []) else heading
        level = int(heading.name[1])
        sibling = wrapper.next_sibling
        while sibling is not None and ((lvl := _heading_level(sibling)) is None or lvl > level):
            following = sibling.next_sibling
            sibling.extract()
            sibling = following
        wrapper.decompose()


def _is_article_title(title: str) -> bool:
    prefix, sep, _rest = title.partition(":")
    namespace = prefix.strip().lower()
    return not (sep and (namespace in _NON_ARTICLE_NAMESPACES or namespace.endswith(" talk")))


def _rewrite_links(root: Tag, host: str) -> list[str]:
    outlinks: list[str] = []
    for anchor in list(root.find_all("a", href=True)):
        href = str(anchor["href"])
        if href.startswith("/wiki/"):
            title = unquote(href[len("/wiki/"):].split("#", 1)[0]).replace("_", " ")
            if not title or not _is_article_title(title):
                anchor.unwrap()
                continue
            url = canonical_article_url(host, title)
            anchor["href"] = url
            outlinks.append(url)
        elif href.startswith("#") or href.startswith("/w/"):
            anchor.unwrap()
        elif href.startswith("//"):
            anchor["href"] = f"https:{href}"
    return list(dict.fromkeys(outlinks))


def _strip_attributes(root: Tag) -> None:
    for tag in root.find_all(True):
        tag.attrs = {k: v for k, v in tag.attrs.items() if k in _KEEP_ATTRIBUTES}


def _flatten_tables(soup: BeautifulSoup) -> None:
    for table in list(soup.find_all("table")):
        if table.parent is None or table.find_parent("table") is not None:
            continue
        rows = []
        for row in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            if any(cells):
                rows.append(" | ".join(cells))
        table.replace_with("\n" + "\n".join(rows) + "\n")


def extract_text(body_html: str, title: str) -> str:
    """Newlines only at block boundaries, so inline links stay inside their
    sentence (a naive `get_text("\\n")` splits a line at every link)."""
    soup = BeautifulSoup(body_html, "lxml")
    # Parser output wraps long paragraphs with literal newlines; those are not breaks.
    for node in list(soup.find_all(string=True)):
        if "\n" in node and node.parent is not None and node.parent.name != "pre":
            node.replace_with(node.replace("\n", " "))
    _flatten_tables(soup)
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(_BLOCK_TAGS):
        block.append("\n")
    lines = (_SPACES.sub(" ", line).strip() for line in soup.get_text().splitlines())
    text = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    return f"{title}\n\n{text}\n"


def _wrap(title: str, source_url: str, revid: int, body: str) -> str:
    safe_title = html.escape(title)
    safe_url = html.escape(source_url, quote=True)
    return (
        "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
        f"<title>{safe_title}</title></head><body>\n<h1>{safe_title}</h1>\n"
        f"<p>Source: <a href=\"{safe_url}\">{safe_url}</a> (revision {revid})</p>\n"
        f"{body}\n</body></html>\n"
    )


def clean_article_html(raw_html: str, *, title: str, host: str, source_url: str, revid: int) -> CleanedArticle:
    soup = BeautifulSoup(raw_html, "lxml")
    root = soup.select_one(".mw-parser-output") or soup.body or soup
    for selector in _STRIP_SELECTORS:
        for node in root.select(selector):
            node.decompose()
    _drop_sections(root)
    outlinks = _rewrite_links(root, host)
    _strip_attributes(root)
    body = root.decode_contents()
    return CleanedArticle(
        html=_wrap(title, source_url, revid, body),
        text=extract_text(body, title),
        outlinks=tuple(outlinks),
    )
