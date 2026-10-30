"""QA probes for the corpus size guard: ``--max-mb`` must be an upper bound.

Measured by independent QA (PX-0): the standard profile writes 150.4 MiB while
``estimate_bytes`` says about 132 MB, and ``--large-mb 20`` writes a 47 MB PDF
(the generator could not make less than one full 36 MiB noise image; fixed) while the estimate counts 20 MiB. A corpus bigger than
``--max-mb`` is therefore written without a refusal.
"""

from __future__ import annotations

from pathlib import Path

from tests.perf.parse_bench import corpus

MIB = corpus.MIB


def test_estimate_covers_the_standard_corpus_that_is_actually_written() -> None:
    # 150.4 MiB: corpus_id 47d73e51ea09ae8e, seed 1, measured from the manifest.
    params = corpus.resolve_params("standard", 1.0, None, None)
    assert corpus.estimate_bytes(params) >= 150 * MIB


def test_estimate_covers_a_small_large_pdf_target(tmp_path: Path) -> None:
    # Was: a 20 MB target wrote 47 MB because the generator could not make less than one full
    # noise image. It now shrinks a single image, so what is written stays under the estimate.
    manifest = corpus.generate(tmp_path, profile="smoke", large_mb=3, max_mb=64)
    params = corpus.resolve_params("smoke", 1.0, 3, None)
    assert manifest.total_bytes <= corpus.estimate_bytes(params)
    large = next(e for e in manifest.entries if e.kind == "large")
    assert large.bytes < 8 * MIB
