"""Shared helpers for splitting oversized text during block parsing.

Also hosts the sentence-boundary and language-detection helpers used by the
indexing pipeline (``app.modules.transformers.vectorstore``) so that block
splitting and sentence sub-chunking share one dependency-light implementation
instead of pulling in a full NLP pipeline (spaCy) for what is fundamentally
rule-based text segmentation.
"""

from __future__ import annotations

import re
import threading

import pysbd
import pysbd.languages
from lingua import Language as LinguaLanguage
from lingua import LanguageDetector, LanguageDetectorBuilder

# Rule-based sentence segmentation (pysbd) has a soft practical limit far below
# spaCy's 1M-char max_length; keep blocks well under what any downstream
# sentence splitter can process in bounded time.
MAX_TEXT_BLOCK_CHARS = 50_000

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def split_long_text(
    text: str,
    max_chars: int = MAX_TEXT_BLOCK_CHARS,
) -> list[str]:
    """Split *text* into chunks at sentence boundaries, each at most *max_chars*."""
    if not text or len(text) <= max_chars:
        return [text] if text else []

    sentences = _SENTENCE_BOUNDARY_RE.split(text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append(" ".join(current))
                current, current_len = [], 0
            for offset in range(0, len(sentence), max_chars):
                chunks.append(sentence[offset : offset + max_chars])
            continue

        added_len = len(sentence) + (1 if current else 0)
        if current_len + added_len > max_chars:
            chunks.append(" ".join(current))
            current, current_len = [sentence], len(sentence)
        else:
            current.append(sentence)
            current_len += added_len

    if current:
        chunks.append(" ".join(current))

    return chunks or [text]


# ---------------------------------------------------------------------------
# Sentence boundary detection (multilingual, rule-based)
# ---------------------------------------------------------------------------

# pysbd's native rule sets. Languages we route to a real rule set (not just
# the "fall back to en" catch-all) live here for clarity/extension.
_PYSBD_SUPPORTED = frozenset(pysbd.languages.LANGUAGE_CODES.keys())

# Common languages pysbd does not natively support, mapped to the closest
# available rule set (script / punctuation conventions are similar enough
# that boundary detection stays reasonable — far better than defaulting
# straight to English rules for e.g. Bengali's Devanagari-style danda).
_LANGUAGE_ALIASES = {
    "pt": "es",  # Portuguese -> Spanish
    "bn": "hi",  # Bengali -> Hindi (shares the danda sentence terminator)
    "id": "en",  # Indonesian -> English (Latin script, standard `.!?`)
    "ko": "en",  # Korean -> English (Latin-style punctuation in practice)
    "vi": "en",  # Vietnamese -> English
    "tr": "en",  # Turkish -> English
}

_SEGMENTER_CACHE = threading.local()


def _get_segmenter(language: str) -> "pysbd.Segmenter":
    lang = _LANGUAGE_ALIASES.get(language, language)
    if lang not in _PYSBD_SUPPORTED:
        lang = "en"
    if not hasattr(_SEGMENTER_CACHE, "cache"):
        _SEGMENTER_CACHE.cache = {}
    segmenter = _SEGMENTER_CACHE.cache.get(lang)
    if segmenter is None:
        # clean=False preserves the original text verbatim so embedded
        # sentences match the source block exactly (no whitespace mangling).
        segmenter = pysbd.Segmenter(language=lang, clean=False)
        _SEGMENTER_CACHE.cache[lang] = segmenter
    return segmenter


def split_into_sentences(text: str, language: str = "en") -> list[str]:
    """Split *text* into sentences using rule-based segmentation.

    Falls back to a regex splitter if pysbd raises on pathological input —
    indexing must never fail solely because sentence sub-chunking errored.
    """
    if not text or not text.strip():
        return []
    try:
        segmenter = _get_segmenter(language)
        return [s for s in segmenter.segment(text) if s and s.strip()]
    except Exception:
        return [s for s in _SENTENCE_BOUNDARY_RE.split(text) if s and s.strip()]


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

# Restrict the detector to languages we can actually route to a sentence-
# boundary rule set (native pysbd support or an alias above). A smaller
# candidate set is both faster and more accurate than the full 75-language
# model, since implausible languages can't be selected.
_DETECTABLE_LANGUAGES = [
    LinguaLanguage.ENGLISH,
    LinguaLanguage.CHINESE,
    LinguaLanguage.HINDI,
    LinguaLanguage.SPANISH,
    LinguaLanguage.FRENCH,
    LinguaLanguage.ARABIC,
    LinguaLanguage.RUSSIAN,
    LinguaLanguage.URDU,
    LinguaLanguage.JAPANESE,
    LinguaLanguage.GERMAN,
    LinguaLanguage.PORTUGUESE,
    LinguaLanguage.ITALIAN,
    LinguaLanguage.DUTCH,
    LinguaLanguage.POLISH,
    LinguaLanguage.BENGALI,
]

_DETECTION_SAMPLE_CHARS = 2000

_detector_cache: dict[str, LanguageDetector] = {}


def _get_detector() -> LanguageDetector:
    detector = _detector_cache.get("detector")
    if detector is None:
        detector = (
            LanguageDetectorBuilder.from_languages(*_DETECTABLE_LANGUAGES)
            # Detection runs on multi-sentence samples, not single words/short
            # chat messages, so the low-accuracy (n-gram-light) mode is a safe
            # speed/accuracy trade-off here.
            .with_low_accuracy_mode()
            .build()
        )
        _detector_cache["detector"] = detector
    return detector


def detect_language(text: str, default: str = "en") -> str:
    """Best-effort ISO 639-1 language code for *text*, defaulting to *default*.

    Never raises: any detector failure or ambiguous/mixed input falls back to
    *default* so indexing is never blocked on language detection.
    """
    sample = (text or "").strip()[:_DETECTION_SAMPLE_CHARS]
    if not sample:
        return default
    try:
        detected = _get_detector().detect_language_of(sample)
    except Exception:
        return default
    if detected is None:
        return default
    return detected.iso_code_639_1.name.lower()
