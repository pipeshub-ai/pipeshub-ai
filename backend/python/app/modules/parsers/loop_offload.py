"""Size thresholds for parse phases that moved off the event loop.

Each phase hops to a thread only at or above its own size (``offload_if_large``): a hop
costs tens of microseconds, which is pure loss on the few-KB records connectors emit in
bulk. One constant per phase, because the phases differ by orders of magnitude in cost
per byte (BeautifulSoup is pure Python, a UTF-8 decode is a memcpy).

TODO(PX-3 M1): every value below is a placeholder, set no lower than the shared default
so that a small record behaves exactly as it did before the phase moved. Replace each from
the per-phase stall measurement of the parse-bench tooling (``tests/perf/parse_bench/px3_bench.py``);
the change does not merge until they are.
"""
from __future__ import annotations

from app.utils.cpu_offload import DEFAULT_OFFLOAD_THRESHOLD_BYTES

# HTML: ``clean_html`` and ``replace_relative_image_urls`` (BeautifulSoup, then ``str(soup)``).
HTML_CLEAN_THRESHOLD_BYTES = DEFAULT_OFFLOAD_THRESHOLD_BYTES
# YAML: the UTF-8 decode of the file before the (already threaded) load.
YAML_DECODE_THRESHOLD_BYTES = DEFAULT_OFFLOAD_THRESHOLD_BYTES
# A dict or list payload serialised to bytes before a JSON or YAML parse has no cheap size
# (``offload_if_large`` reports an object it cannot measure as large), so it always hops and
# has no threshold here. TODO(PX-3 M1): if the hop shows up on 4 KB records, add a cheap
# size estimate for it.
