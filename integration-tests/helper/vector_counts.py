"""Reading a set of per-document vector counts that should all have been equal.

Split out from the resilience suite so the reasoning can be tested on its own:
the interesting cases are distributions the suite produces only when something
has already gone wrong, which is a bad time to be finding out the explanation
is wrong too.
"""

from __future__ import annotations

from collections import Counter


def describe_divergence(counts: dict[str, int]) -> str | None:
    """Explain why these counts differ, or None when they do not.

    A document short of vectors has content that cannot be found; one carrying
    extra has a passage indexed more than once. They are different bugs, so the
    verdict is only given when one count is strictly more common than every
    other -- the majority is then a fair stand-in for what should have been
    written.

    On a tie it says nothing about direction. With eight documents a four-four
    split between two counts is an ordinary shape of this failure, and picking a
    side by frequency alone would name whichever count happened to be seen
    first, which is upload order. Reporting the wrong bug confidently is worse
    than reporting the distribution and stopping.
    """
    distinct = set(counts.values())
    if len(distinct) <= 1:
        return None

    frequencies = Counter(counts.values()).most_common()
    top_count, top_frequency = frequencies[0]
    tied = [c for c, f in frequencies if f == top_frequency]

    if len(tied) > 1:
        groups = "; ".join(
            f"{count} vectors: {sorted(r for r, n in counts.items() if n == count)}"
            for count in sorted(distinct)
        )
        return (
            f"no count is more common than the rest, so which of these is correct "
            f"cannot be told apart here -- {groups}"
        )

    short = {r: n for r, n in counts.items() if n < top_count}
    extra = {r: n for r, n in counts.items() if n > top_count}
    parts = [f"most hold {top_count}"]
    if short:
        parts.append(f"missing chunks, so part of the document cannot be found: {short}")
    if extra:
        parts.append(f"extra chunks, so a passage is indexed more than once: {extra}")
    return "; ".join(parts)
