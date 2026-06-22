"""Shared HTML URL helpers for parser backends."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


def get_base_url_from_html(soup: BeautifulSoup) -> str | None:
    """Extract base URL from HTML using multiple fallback strategies."""
    base_tag = soup.find("base", href=True)
    if base_tag:
        return base_tag["href"]

    canonical = soup.find("link", rel="canonical", href=True)
    if canonical:
        canonical_url = canonical["href"]
        parsed = urlparse(canonical_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    for tag_name, attr in [
        ("link", "href"),
        ("script", "src"),
        ("img", "src"),
        ("a", "href"),
    ]:
        for tag in soup.find_all(tag_name):
            if tag.get(attr):
                url = tag[attr]
                parsed = urlparse(url)
                if parsed.scheme and parsed.netloc:
                    return f"{parsed.scheme}://{parsed.netloc}"

    return None


def replace_relative_image_urls(html_string: str) -> str:
    """Replace relative ``img`` ``src`` values with absolute URLs when a base is inferable."""
    soup = BeautifulSoup(html_string, "html.parser")
    base_url = get_base_url_from_html(soup)

    if not base_url:
        return html_string

    for img in soup.find_all("img"):
        if img.get("src"):
            original_url = img["src"]
            parsed = urlparse(original_url)

            if not parsed.scheme:
                img["src"] = urljoin(base_url, original_url)

    return str(soup)
