"""Which URLs name the same page.

A page is fetched and stored under its URL with the fragment and the query parameters that don't
change what the page shows removed: click and campaign trackers always, plus any the connector is
told to ignore. Two links that differ only in those reach the same record.
"""

from collections.abc import Collection
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Query parameter names, or name prefixes ending in "_", that only track where a visit came from.
TRACKING_PARAMS = frozenset({
    "utm_", "gclid", "gclsrc", "dclid", "gbraid", "wbraid", "fbclid", "msclkid", "yclid",
    "twclid", "ttclid", "li_fat_id", "igshid", "mc_cid", "mc_eid", "_ga", "_gl", "_hsenc",
    "_hsmi", "hsctatracking", "mkt_tok", "oly_anon_id", "oly_enc_id", "vero_id", "rb_clickid",
    "s_cid", "spm",
})
# Set as the only ignored parameter, drops every query parameter.
ALL_PARAMS = "*"


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or any(
        lowered.startswith(prefix) for prefix in TRACKING_PARAMS if prefix.endswith("_")
    )


def clean_url(url: str, ignored_params: Collection[str] = ()) -> str:
    """``url`` without its fragment, tracking parameters and ``ignored_params``.

    ``ignored_params`` holds parameter names matched case-blind, or ``ALL_PARAMS``. Kept parameters
    stay in the site's order, since a few servers care about it.
    """
    parsed = urlparse(url)
    ignored = {p.lower() for p in ignored_params}
    if ALL_PARAMS in ignored:
        query = ""
    elif parsed.query:
        kept = [
            (name, value)
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not _is_tracking(name) and name.lower() not in ignored
        ]
        query = parsed.query if len(kept) == len(parse_qsl(parsed.query, keep_blank_values=True)) else urlencode(kept)
    else:
        query = ""
    return urlunparse(parsed._replace(query=query, fragment=""))


def is_same_page(canonical: str, page_url: str) -> bool:
    """Whether a page's ``rel=canonical`` names the page itself, give or take its query string.

    Only then is it followed: a canonical pointing at another path is often a hub or a section
    index that many pages share, and following it would fold them into one record.
    """
    a, b = urlparse(canonical), urlparse(page_url)
    return (
        a.scheme.lower() == b.scheme.lower()
        and a.netloc.lower() == b.netloc.lower()
        and (a.path.rstrip("/") or "/") == (b.path.rstrip("/") or "/")
    )
