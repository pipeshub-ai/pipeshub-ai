"""CSR (client-side rendering) detection via browser-native text comparison.

Both snapshots come from the browser's ``innerText`` (which respects CSS
visibility), eliminating false results from CSS-hidden elements that
BeautifulSoup cannot detect.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RenderAnalysis:
    raw_text_length: int
    rendered_text_length: int
    text_ratio: float
    verdict: str
    confidence: str

    @property
    def is_csr(self) -> bool:
        return self.verdict == "CSR"


# JS injected via BrowserConfig.init_scripts — runs before any page JS.
PRE_HYDRATION_INIT_SCRIPT = """
document.addEventListener('DOMContentLoaded', () => {
    window.__preHydrationLen = (document.body.innerText || '').trim().length;
}, { once: true });
"""

# JS executed as ``js_code`` after the page has fully rendered.
# crawl4ai wraps this in an async IIFE, so bare ``return`` works.
CSR_PROBE_JS = (
    "return {"
    "  preLen: window.__preHydrationLen ?? -1,"
    "  postLen: (document.body.innerText || '').trim().length"
    "};"
)


def analyze_rendering(pre_len: int, post_len: int) -> RenderAnalysis:
    """Compare pre-hydration vs post-hydration visible-text lengths.

    Both values come from ``document.body.innerText`` captured inside the
    same browser context, so CSS visibility is already accounted for.
    """
    if post_len == 0:
        text_ratio = 1.0
    else:
        text_ratio = min(pre_len / post_len, 1.0)

    if pre_len < 50 and post_len >= 200:
        verdict, confidence = "CSR", "high"
    elif text_ratio < 0.1 and post_len >= 200:
        verdict, confidence = "CSR", "high"
    elif text_ratio < 0.3 and post_len >= 150:
        verdict, confidence = "CSR", "medium"
    elif text_ratio > 0.7:
        verdict, confidence = "SSR", "high"
    elif text_ratio > 0.5:
        verdict, confidence = "CSR", "medium"
    else:
        verdict, confidence = "CSR", "low"

    return RenderAnalysis(
        raw_text_length=pre_len,
        rendered_text_length=post_len,
        text_ratio=text_ratio,
        verdict=verdict,
        confidence=confidence,
    )
