"""Normalising the dataset's Wikipedia links.

The 824 rows reference articles in several shapes: desktop and mobile hosts,
titles percent-encoded once or twice (`%2527`), `#section` and `#:~:text=`
anchors, `index.php?title=` forms, a `w.wiki` short link, a
`simple.wikipedia` article, a `Special:Search` URL, and list cells holding
several comma-joined URLs. `WikiRef.key` is the identity used to match a
dataset link to a pinned corpus article.
"""

from __future__ import annotations

import ast
import re
from typing import Literal
from urllib.parse import parse_qs, quote, unquote, urlsplit

from pydantic import BaseModel, ConfigDict

_SPLIT_RE = re.compile(r",\s*(?=https?://)")
_MAX_UNQUOTE_PASSES = 3
_SHORT_LINK_HOSTS = frozenset({"w.wiki"})
_TITLE_SAFE_CHARS = "_()',!:;@$*+-./~"

RefKind = Literal["article", "search", "short_link"]


class WikiRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    raw: str
    kind: RefKind
    host: str
    # Display form: spaces, not underscores. Search refs carry the query here.
    title: str = ""
    fragment: str | None = None

    @property
    def key(self) -> str:
        if self.kind != "article":
            return f"{self.kind}:{self.raw.strip()}"
        return f"{self.host}/{title_to_path(self.title)}"

    @property
    def canonical_url(self) -> str:
        return canonical_article_url(self.host, self.title)


def split_link_field(raw: str) -> list[str]:
    return [part.strip() for part in _SPLIT_RE.split(raw.strip()) if part.strip()]


def parse_wiki_links_cell(cell: str) -> list[str]:
    """`wiki_links` is a stringified Python list; some elements hold several URLs."""
    if not cell or not cell.strip():
        return []
    try:
        values = ast.literal_eval(cell)
    except (ValueError, SyntaxError):
        values = [cell]
    if isinstance(values, str):
        values = [values]
    return [url for value in values for url in split_link_field(str(value))]


def unquote_fully(text: str) -> str:
    for _ in range(_MAX_UNQUOTE_PASSES):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    return text


def title_to_path(title: str) -> str:
    """MediaWiki's canonical path form: underscores, first letter upper-cased."""
    path = title.strip().replace(" ", "_")
    return path[:1].upper() + path[1:]


def canonical_article_url(host: str, title: str) -> str:
    return f"https://{host}/wiki/{quote(title_to_path(title), safe=_TITLE_SAFE_CHARS)}"


def _desktop_host(netloc: str) -> str:
    host = netloc.lower().split(":", 1)[0]
    return host.replace(".m.wikipedia.org", ".wikipedia.org")


def normalize_wiki_url(raw: str) -> WikiRef:
    text = raw.strip()
    # One dataset link omits the scheme (`en.wikipedia.org/wiki/...`).
    parts = urlsplit(text if "://" in text else f"https://{text}")
    host = _desktop_host(parts.netloc)
    if host in _SHORT_LINK_HOSTS:
        return WikiRef(raw=raw, kind="short_link", host=host)

    query = parse_qs(parts.query)
    path = unquote_fully(parts.path)
    if path.startswith("/wiki/"):
        title = path[len("/wiki/"):]
    else:
        title = unquote_fully(query.get("title", [""])[0])
    title = title.replace("_", " ").strip()

    if title.lower().startswith("special:search"):
        search = unquote_fully(query.get("search", [""])[0]) or title.partition("/")[2]
        return WikiRef(raw=raw, kind="search", host=host, title=search.replace("_", " ").strip())
    return WikiRef(
        raw=raw, kind="article", host=host, title=title,
        fragment=unquote_fully(parts.fragment) or None,
    )
