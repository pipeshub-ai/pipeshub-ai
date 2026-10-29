"""QA probes for the corpus size guard: ``--max-mb`` must be an upper bound.

Measured by independent QA (PX-0): the standard profile writes 150.4 MiB while
``estimate_bytes`` says about 132 MB, and ``--large-mb 20`` writes a 47 MB PDF
(one 36 MiB incompressible image per started *target*, so the file is never
smaller than one image) while the estimate counts 20 MiB. A corpus bigger than
``--max-mb`` is therefore written without a refusal.
"""

from __future__ import annotations

import pytest

from tests.perf.parse_bench import corpus

MIB = corpus.MIB


@pytest.mark.xfail(strict=True, reason="PX-0 QA D-1: estimate_bytes under-counts the standard corpus")
def test_estimate_covers_the_standard_corpus_that_is_actually_written() -> None:
    # 150.4 MiB: corpus_id 47d73e51ea09ae8e, seed 1, measured from the manifest.
    params = corpus.resolve_params("standard", 1.0, None, None)
    assert corpus.estimate_bytes(params) >= 150 * MIB


@pytest.mark.xfail(strict=True, reason="PX-0 QA D-1: estimate_bytes under-counts a small --large-mb")
def test_estimate_covers_a_small_large_pdf_target() -> None:
    # large_pdf(seed, 20) is about 47 MB: the generator cannot make less than one 4096x3072 noise image.
    params = corpus.resolve_params("smoke", 1.0, 20, None)
    assert corpus.estimate_bytes(params) >= 45 * MIB
