"""QA probes for the corpus size guard: ``--max-mb`` must be an upper bound.

Independent QA (PX-0) measured that the standard profile writes 150.4 MiB while
``estimate_bytes`` says about 132 MB, and ``--large-mb 20`` writes a 47 MB PDF
(the generator could not make less than one full 36 MiB noise image; fixed) while
the estimate counts 20 MiB. The estimate is an early check; ``_put`` checks the
actual bytes before each write and refuses a corpus that would exceed ``--max-mb``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.perf.parse_bench import corpus

if TYPE_CHECKING:
    from pathlib import Path

MIB = corpus.MIB


def test_estimate_covers_a_small_large_pdf_target(tmp_path: Path) -> None:
    # Was: a 20 MB target wrote 47 MB because the generator could not make less than one full
    # noise image. It now shrinks a single image, so what is written stays under the estimate.
    manifest = corpus.generate(tmp_path, profile="smoke", large_mb=3, max_mb=64)
    params = corpus.resolve_params("smoke", 1.0, 3, None)
    assert manifest.total_bytes <= corpus.estimate_bytes(params)
    large = next(e for e in manifest.entries if e.kind == "large")
    assert large.bytes < 8 * MIB
