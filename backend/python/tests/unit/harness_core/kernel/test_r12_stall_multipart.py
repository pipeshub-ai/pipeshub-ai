"""Round 12 (WP-62 x C-1 tool images): a tool round whose result carries images is weighed by the stall counter.

WP-62 skips a call refused only because the web allowance is spent when it counts stalled rounds. It read the
result as text, but a tool that returns images (a chart, a page's figures) gives a list of parts, and the run
failed with a TypeError on the first such round (``test_durable_images.py`` C-1 on the merged tree).

R12-STALL-1 a multipart result is weighed: it is never an allowance refusal unless its text parts say so, and the
            round counts as a round.
"""

from __future__ import annotations

import json

from harness_core.kernel.core.messages import ImagePart, ImageSource, TextPart
from harness_core.kernel.core.stall import (
    ALLOWANCE_EXHAUSTED,
    RoundCall,
    StallState,
    is_allowance_refusal,
    next_stall,
)

IMAGE = ImagePart(source=ImageSource(type="base64", media_type="image/png", data="iVBORw0KGgo="))


def _call(content: object) -> RoundCall:
    return RoundCall("charts__render", "d1", False, content)


def test_r12_stall_1_a_result_with_images_is_weighed_and_read_by_its_text() -> None:
    images = [TextPart(text="Q3 revenue chart"), IMAGE]
    assert not is_allowance_refusal(_call(images))
    refused = [TextPart(text=json.dumps({"error_type": ALLOWANCE_EXHAUSTED})), IMAGE]
    assert is_allowance_refusal(_call(refused))
    after = next_stall(StallState(0, 0, ""), [_call(images)])
    assert isinstance(after, StallState)
