"""Measures for comparing two ways of embedding the same corpus.

The question is whether swapping the weight precision of the embedding model
changes what search returns. Everything here is deterministic given its seeds,
takes plain arrays, and knows nothing about how the vectors were made: the
integration test feeds it vectors produced through the real server and client.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

Encoder = Callable[[list[str]], np.ndarray]

# ---------------------------------------------------------------------------
# Vector and ranking measures
# ---------------------------------------------------------------------------


def unit(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.where(norms == 0, 1.0, norms)


def cosine_rows(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine between matching rows."""
    return np.sum(unit(a.astype(np.float64)) * unit(b.astype(np.float64)), axis=1)


def cosine_stats(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    cos = cosine_rows(a, b)
    return {
        "mean": float(cos.mean()),
        "sd": float(cos.std(ddof=1)) if len(cos) > 1 else 0.0,
        "min": float(cos.min()),
        "p1": float(np.percentile(cos, 1)),
        "n": float(len(cos)),
    }


def rank_documents(queries: np.ndarray, docs: np.ndarray, k: int) -> np.ndarray:
    """Indices of the k best documents per query by cosine, best first; ties go to the lower index."""
    scores = unit(queries.astype(np.float64)) @ unit(docs.astype(np.float64)).T
    order = np.lexsort((np.broadcast_to(np.arange(docs.shape[0]), scores.shape), -scores), axis=1)
    return order[:, :k]


def overlap_at_k(reference: np.ndarray, other: np.ndarray, k: int) -> np.ndarray:
    """Per query, the share of the reference top-k that the other ranking also has in its top-k."""
    return np.array([len(set(r[:k]) & set(o[:k])) / k for r, o in zip(reference, other, strict=True)])


def top1_agreement(reference: np.ndarray, other: np.ndarray) -> np.ndarray:
    return (reference[:, 0] == other[:, 0]).astype(np.float64)


def hit_at_k(ranked: np.ndarray, gold: Sequence[set[int]], k: int) -> np.ndarray:
    """1 when any accepted answer is in the top-k, else 0, per query."""
    return np.array([1.0 if set(row[:k]) & g else 0.0 for row, g in zip(ranked, gold, strict=True)])


def paired_bootstrap_ci(
    a: np.ndarray, b: np.ndarray | None = None, *, resamples: int = 2000, seed: int = 0, level: float = 0.95,
) -> tuple[float, float, float]:
    """Mean of ``a`` (or of ``a - b``, paired per query) with a bootstrap interval over queries."""
    values = np.asarray(a, dtype=np.float64) if b is None else np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(resamples, len(values)))
    means = values[idx].mean(axis=1)
    tail = (1 - level) / 2
    return float(values.mean()), float(np.quantile(means, tail)), float(np.quantile(means, 1 - tail))


def score_drift(reference_queries: np.ndarray, other_queries: np.ndarray, reference_docs: np.ndarray, other_docs: np.ndarray, top: int) -> dict[str, float]:
    """Absolute change of query-document scores over each query's reference top-``top`` documents."""
    ref_scores = unit(reference_queries.astype(np.float64)) @ unit(reference_docs.astype(np.float64)).T
    other_scores = unit(other_queries.astype(np.float64)) @ unit(other_docs.astype(np.float64)).T
    best = np.argsort(-ref_scores, axis=1)[:, :top]
    drift = np.abs(np.take_along_axis(ref_scores, best, 1) - np.take_along_axis(other_scores, best, 1)).ravel()
    ranked = np.sort(ref_scores, axis=1)[:, ::-1]
    gaps = (ranked[:, :-1] - ranked[:, 1:])[:, :10]
    return {
        "median": float(np.median(drift)),
        "p99": float(np.percentile(drift, 99)),
        "max": float(drift.max()),
        "median_adjacent_gap_top10": float(np.median(gaps)),
    }


def rank_flip_rate(gold_scores: np.ndarray, rival_scores: np.ndarray, reference_gold: np.ndarray, reference_rival: np.ndarray) -> float:
    """How often the order of a gold chunk and its nearest hard negative differs from the reference order."""
    flipped = (gold_scores > rival_scores) != (reference_gold > reference_rival)
    return float(flipped.mean())


# ---------------------------------------------------------------------------
# Mixed index: the same chunks embedded both ways in one collection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MixedIndexResult:
    gap_mean: float
    gap_p99: float
    abs_gap_mean: float
    new_outranks_old: float
    pairs: int


def mixed_index_pairs(queries: np.ndarray, old_docs: np.ndarray, new_docs: np.ndarray, top: int) -> MixedIndexResult:
    """Both copies of every chunk sit in one index and every query is embedded the new way.

    For each query's ``top`` best chunks by the old copy, compare the score of
    the new copy with the old copy, and check which copy the index ranks higher.
    A rate of 0.5 means neither copy is favoured; a gap of 0 means scores agree.
    """
    q = unit(queries.astype(np.float64))
    old = q @ unit(old_docs.astype(np.float64)).T
    new = q @ unit(new_docs.astype(np.float64)).T
    best = np.argsort(-old, axis=1)[:, :top]
    s_old = np.take_along_axis(old, best, 1).ravel()
    s_new = np.take_along_axis(new, best, 1).ravel()
    gap = s_new - s_old
    outranks = np.where(s_new > s_old, 1.0, np.where(s_new == s_old, 0.5, 0.0))
    return MixedIndexResult(
        gap_mean=float(gap.mean()),
        gap_p99=float(np.percentile(gap, 99)),
        abs_gap_mean=float(np.abs(gap).mean()),
        new_outranks_old=float(outranks.mean()),
        pairs=int(gap.size),
    )


def random_split(count: int, seed: int, share_new: float = 0.5) -> np.ndarray:
    """Boolean mask, True where a chunk is embedded the new way (a chunk-wise split)."""
    return np.random.default_rng(seed).random(count) < share_new


def split_index(old_docs: np.ndarray, new_docs: np.ndarray, new_mask: np.ndarray) -> np.ndarray:
    return np.where(new_mask[:, None], new_docs, old_docs)


# ---------------------------------------------------------------------------
# Controls: encoders that must be caught
# ---------------------------------------------------------------------------


def degrade(vectors: np.ndarray, noise: float, seed: int) -> np.ndarray:
    """A deliberately worse encoder: gaussian noise on every vector, renormalised."""
    rng = np.random.default_rng(seed)
    return unit(vectors + noise * rng.standard_normal(vectors.shape) / math.sqrt(vectors.shape[1]))


def biased(vectors: np.ndarray, strength: float, seed: int) -> np.ndarray:
    """An encoder that pulls every vector towards one fixed direction."""
    direction = unit(np.random.default_rng(seed).standard_normal((1, vectors.shape[1])))
    return unit(vectors + strength * direction)


# ---------------------------------------------------------------------------
# Corpora and queries (deterministic, seeded)
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_'-]+")
_FILLER = frozenset(
    "the and for that with this from are was were not you your have has will can its into when then than "
    "they them their which also but any all use used using each only more other such these those would should".split()
)


def corpus_sha256(texts: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for text in texts:
        digest.update(text.encode())
        digest.update(b"\x00")
    return digest.hexdigest()


def markdown_chunks(root: Path, count: int, seed: int, words: int = 200) -> list[str]:
    """About ``words``-word chunks of the Markdown files under ``root``, sampled with ``seed``.

    Files are read in sorted order so the same tree and seed give the same chunks.
    """
    chunks: list[str] = []
    for path in sorted(root.rglob("*.md")):
        if any(part in {"node_modules", ".git"} for part in path.parts):
            continue
        tokens = path.read_text(errors="ignore").split()
        for start in range(0, max(0, len(tokens) - words // 2), words):
            piece = tokens[start:start + words]
            if len(piece) >= words // 2:
                chunks.append(" ".join(piece))
    if len(chunks) < count:
        raise ValueError(f"only {len(chunks)} chunks under {root}, need {count}")
    return random.Random(seed).sample(chunks, count)


def number_heavy_chunks(count: int, seed: int) -> list[str]:
    """Tables, CSV rows and code with many numbers and identifiers, built from templates."""
    rng = random.Random(seed)
    makers: list[Callable[[random.Random], str]] = [_csv_chunk, _table_chunk, _code_chunk]
    return [makers[i % len(makers)](rng) for i in range(count)]


def _csv_chunk(rng: random.Random) -> str:
    rows = [f"{rng.randint(1000, 9999)},{rng.choice(('alpha', 'bravo', 'cedar', 'delta'))}-{rng.randint(1, 99)},"
            f"{rng.uniform(0, 5000):.2f},{rng.randint(2015, 2025)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
            for _ in range(rng.randint(14, 24))]
    return "id,code,amount,date\n" + "\n".join(rows)


def _table_chunk(rng: random.Random) -> str:
    rows = [f"| {rng.choice(('north', 'south', 'east', 'west'))} | Q{rng.randint(1, 4)} {rng.randint(2018, 2025)} | "
            f"{rng.randint(10, 9999)} | {rng.uniform(0, 100):.1f}% |" for _ in range(rng.randint(12, 20))]
    return "| region | period | units | share |\n|---|---|---|---|\n" + "\n".join(rows)


def _code_chunk(rng: random.Random) -> str:
    name = rng.choice(("retry", "batch", "limit", "window", "score"))
    lines = [f"def {name}_{rng.randint(1, 99)}(value, factor={rng.randint(2, 9)}):"]
    for _ in range(rng.randint(8, 14)):
        lines.append(f"    value = value * {rng.randint(2, 97)} + {rng.randint(1, 500)}  # step {rng.randint(1, 40)}")  # noqa: PERF401
    lines.append("    return value")
    return "\n".join(lines)


def long_chunks(sources: Sequence[str], count: int, seed: int, words: int = 380) -> list[str]:
    """Chunks near the model's 512-token limit, so truncation-length error is exercised."""
    rng = random.Random(seed)
    pool = [w for text in sources for w in text.split()]
    if len(pool) < words:
        raise ValueError("not enough source words for a long chunk")
    out = []
    for _ in range(count):
        start = rng.randrange(0, len(pool) - words)
        out.append(" ".join(pool[start:start + words]))
    return out


def near_duplicates(chunks: Sequence[str], count: int, seed: int) -> tuple[list[str], list[int]]:
    """Near copies of some chunks (one sentence-sized edit each) and the index each copy was made from."""
    rng = random.Random(seed)
    sources = rng.sample(range(len(chunks)), count)
    copies = []
    for i in sources:
        words = chunks[i].split()
        at = rng.randrange(len(words))
        words[at:at + 3] = ["(revised)", str(rng.randint(2, 98)), "items"]
        copies.append(" ".join(words))
    return copies, sources


def paraphrase_queries(chunks: Sequence[str], count: int, seed: int) -> tuple[list[str], list[int]]:
    """One query per sampled chunk from templates over the chunk's rarest terms.

    A query is built from three words that are rare across the corpus, never
    from the chunk's own first sentence or heading, so it is not a near copy of
    its answer. Returns the queries and the index of each one's chunk.
    """
    frequency: dict[str, int] = {}
    tokenised = []
    for text in chunks:
        words = {w.lower() for w in _WORD.findall(text) if len(w) > 3 and w.lower() not in _FILLER}
        tokenised.append(words)
        for w in words:
            frequency[w] = frequency.get(w, 0) + 1
    templates = (
        "how does {a} relate to {b} and {c}",
        "what is the behaviour of {a} when {b} and {c} are involved",
        "explain {a} together with {b} for {c}",
        "which setting controls {a} and {b} in the context of {c}",
        "notes about {a}, {b} and {c}",
    )
    rng = random.Random(seed)
    queries, gold = [], []
    for i in rng.sample(range(len(chunks)), count):
        first_sentence = re.split(r"(?<=[.!?])\s", chunks[i], maxsplit=1)[0].lower()
        usable = sorted((w for w in tokenised[i] if w not in first_sentence), key=lambda w: (frequency[w], w))[:12]
        if len(usable) < 3:
            usable = sorted(tokenised[i], key=lambda w: (frequency[w], w))[:12]
        if len(usable) < 3:
            continue
        a, b, c = rng.sample(usable, 3)
        queries.append(rng.choice(templates).format(a=a, b=b, c=c))
        gold.append(i)
    return queries, gold


def hard_negative_sets(reference_docs: np.ndarray, gold: Sequence[int], threshold: float = 0.95) -> list[set[int]]:
    """Per query, the gold chunk plus every chunk whose reference cosine to the gold exceeds ``threshold``."""
    docs = unit(reference_docs.astype(np.float64))
    sims = docs @ docs.T
    return [{g, *map(int, np.nonzero(sims[g] > threshold)[0])} for g in gold]


def nearest_rival(reference_docs: np.ndarray, gold: Sequence[int]) -> np.ndarray:
    """For each gold chunk, the other chunk most similar to it in the reference embedding."""
    docs = unit(reference_docs.astype(np.float64))
    sims = docs @ docs.T
    np.fill_diagonal(sims, -np.inf)
    return np.array([int(np.argmax(sims[g])) for g in gold])
