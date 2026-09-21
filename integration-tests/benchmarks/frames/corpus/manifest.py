"""Corpus manifest persistence and file naming."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from benchmarks.frames.models import CorpusManifest
from benchmarks.frames.store import atomic_write_text

MANIFEST_FILE = "corpus_manifest.json"
HTML_DIR = "html"
TEXT_DIR = "text"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_STEM = 120


def safe_filename(title: str, canonical_url: str) -> str:
    """`{Title}--{sha1(url)[:8]}.html`: the hash suffix keeps names unique even
    where titles differ only by case, which PipesHub KB uploads would skip as
    duplicates, and a `/` in a title can never create a folder."""
    stem = _UNSAFE.sub("_", title).strip("._")[:_MAX_STEM] or "article"
    digest = hashlib.sha1(canonical_url.encode()).hexdigest()[:8]  # noqa: S324 — naming, not security
    return f"{stem}--{digest}.html"


def text_filename(html_filename: str) -> str:
    return html_filename.removesuffix(".html") + ".txt"


def record_name(html_filename: str) -> str:
    """PipesHub stores an uploaded file's name without its extension."""
    return html_filename.removesuffix(".html")


def save_manifest(corpus_dir: Path, manifest: CorpusManifest) -> None:
    atomic_write_text(corpus_dir / MANIFEST_FILE, manifest.model_dump_json(indent=1) + "\n")


def load_manifest(corpus_dir: Path) -> CorpusManifest:
    return CorpusManifest.model_validate_json((corpus_dir / MANIFEST_FILE).read_text())


def read_article_text(corpus_dir: Path, text_file: str) -> str:
    return (corpus_dir / TEXT_DIR / text_file).read_text(encoding="utf-8")


def read_article_html(corpus_dir: Path, html_file: str) -> bytes:
    return (corpus_dir / HTML_DIR / html_file).read_bytes()
