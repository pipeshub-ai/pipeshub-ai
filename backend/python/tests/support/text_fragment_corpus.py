"""The golden corpus for text-fragment generation, shared by pytest and the regen script.

A case pairs the block text PipesHub stores (`snippet`, in `format`) with the
HTML page the user lands on. `expected_url` snapshots what the generator
emits; `expected_highlight` is the text a browser must highlight for that URL,
which the reference matcher checks independently of the generator.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.utils.text_fragments import SourceFormat, TextFragmentGenerator
from tests.support.text_fragment_matcher import highlight_for_url

CORPUS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "text_fragments"
CASES_FILE = CORPUS_DIR / "cases.json"
HTML_DIR = CORPUS_DIR / "html"
FAKE_ORIGIN = "https://pages.example.test"


@dataclass(frozen=True)
class Case:
    id: str
    fixture: str
    format: SourceFormat
    snippet: str
    base_url: str
    expected_url: str | None
    expected_highlight: str | None
    raw: dict

    @property
    def page_html(self) -> str:
        return (HTML_DIR / f"{self.fixture}.html").read_text(encoding="utf-8")


def load_raw_cases() -> list[dict]:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def load_cases() -> list[Case]:
    return [
        Case(
            id=raw["id"],
            fixture=raw["fixture"],
            format=SourceFormat(raw["format"]),
            snippet=raw["snippet"],
            base_url=raw.get("base_url") or f"{FAKE_ORIGIN}/{raw['fixture']}.html",
            expected_url=raw.get("expected_url"),
            expected_highlight=raw.get("expected_highlight"),
            raw=raw,
        )
        for raw in load_raw_cases()
    ]


def generate_url(case: Case, generator: TextFragmentGenerator | None = None) -> str:
    return (generator or TextFragmentGenerator()).build_url(case.base_url, case.snippet, case.format)


def highlight_of(case: Case, url: str) -> str | None:
    return highlight_for_url(case.page_html, url)


def dump_cases(raw_cases: list[dict]) -> str:
    """Serialize with invisible characters escaped, so a diff shows what changed."""
    text = json.dumps(raw_cases, ensure_ascii=False, indent=2)
    return "".join(_escape(char) for char in text) + "\n"


def _escape(char: str) -> str:
    category = unicodedata.category(char)
    if char != " " and category in ("Zs", "Zl", "Zp", "Cf", "Mn", "Cc") and char != "\n":
        return json.dumps(char, ensure_ascii=True)[1:-1]
    return char
