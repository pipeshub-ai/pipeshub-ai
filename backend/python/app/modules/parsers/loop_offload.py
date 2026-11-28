"""Size thresholds for parse phases that moved off the event loop.

Each phase hops to a thread only at or above its own size (``offload_if_large``): a hop
costs tens of microseconds, which is pure loss on the few-KB records connectors emit in
bulk. One constant per phase, because the phases differ by orders of magnitude in cost
per byte (BeautifulSoup is pure Python, a UTF-8 decode is a memcpy).

Every value below is a conservative placeholder, set no lower than the shared default so that
a small record behaves exactly as it did before the phase moved. A follow-up replaces each with
a value taken from the per-phase stall measurement of the parse-bench tooling
(``tests/perf/parse_bench/px3_bench.py``). Until then a page between the measured stall size
and the placeholder still runs inline, as it did before this change.
"""
from __future__ import annotations

from app.utils.cpu_offload import DEFAULT_OFFLOAD_THRESHOLD_BYTES, offload_if_large

# HTML: ``clean_html`` and ``replace_relative_image_urls`` (BeautifulSoup, then ``str(soup)``).
HTML_CLEAN_THRESHOLD_BYTES = DEFAULT_OFFLOAD_THRESHOLD_BYTES
# YAML: the UTF-8 decode of the file before the (already threaded) load.
YAML_DECODE_THRESHOLD_BYTES = DEFAULT_OFFLOAD_THRESHOLD_BYTES
# A dict or list payload serialised to bytes before a JSON or YAML parse has no cheap size
# (``offload_if_large`` reports an object it cannot measure as large), so it always hops and
# has no threshold here. Follow-up: if the hop shows up on 4 KB records, add a cheap
# size estimate for it.


async def html_pass(html_parser: object, method: str, html: str, threshold_bytes: int | None = None) -> str:
    """Run a whole-document HTML pass (``clean_html``, ``replace_relative_image_urls``).

    BeautifulSoup parses and re-serialises the whole page in pure Python, which holds the
    event loop for as long as the page is large. A backend that says it is safe to run these
    on a thread (``PASSES_MAY_RUN_OFF_LOOP is True``) does so at or above the threshold
    (default ``HTML_CLEAN_THRESHOLD_BYTES``); any other backend, or a stand-in that does not set
    the flag to exactly ``True``, runs it inline as it always did.
    """
    fn = getattr(html_parser, method)
    if getattr(html_parser, "PASSES_MAY_RUN_OFF_LOOP", False) is True:
        limit = HTML_CLEAN_THRESHOLD_BYTES if threshold_bytes is None else threshold_bytes
        return await offload_if_large(fn, html, threshold_bytes=limit)
    return fn(html)
