"""Corpus: HTML cleaning and the resumable builder."""

from __future__ import annotations

from pathlib import Path

import pytest
from frames_testkit import FakeArticleSource, html_page

from benchmarks.harness.config import FRAMES_SNAPSHOT
from benchmarks.datasets.frames.builder import CorpusBuilder
from benchmarks.datasets.frames.cleaner import clean_article_html
from benchmarks.harness.corpus.manifest import load_manifest, safe_filename
from benchmarks.datasets.frames.urls import normalize_wiki_url
from benchmarks.harness.errors import CorpusError

ARTICLE = html_page("""
<div class="hatnote">For other uses, see X.</div>
<table class="infobox"><tr><th>Capital</th><td>Fredville</td></tr></table>
<p>Freedonia is ruled by <a href="/wiki/Rufus_T._Firefly">Rufus T. Firefly</a><sup class="reference">[1]</sup>
and borders <a href="/wiki/Sylvania#History">Sylvania</a>. See <a href="/wiki/File:Flag.png">flag</a>
and <a href="#Economy">below</a>.</p>
<figure><img src="//upload.wikimedia.org/flag.png"/><figcaption>Flag</figcaption></figure>
<div class="mw-heading mw-heading2"><h2 id="Economy">Economy</h2><span class="mw-editsection">[edit]</span></div>
<table class="wikitable"><tr><th>Year</th><th>GDP</th></tr><tr><td>1933</td><td>12</td></tr></table>
<div class="mw-heading mw-heading2"><h2 id="References">References</h2></div>
<div class="reflist"><ol><li>A source</li></ol></div>
<p>Trailing reference text that must go.</p>
<div class="navbox">Nav stuff</div>
""")


class TestCleaner:
    def test_keeps_content_tables_and_links(self) -> None:
        cleaned = clean_article_html(
            ARTICLE, title="Freedonia", host="en.wikipedia.org",
            source_url="https://en.wikipedia.org/wiki/Freedonia", revid=42,
        )
        assert "<title>Freedonia</title>" in cleaned.html
        assert 'class="wikitable"' in cleaned.html and 'class="infobox"' in cleaned.html
        assert 'href="https://en.wikipedia.org/wiki/Rufus_T._Firefly"' in cleaned.html
        assert cleaned.outlinks == (
            "https://en.wikipedia.org/wiki/Rufus_T._Firefly", "https://en.wikipedia.org/wiki/Sylvania",
        )

    def test_strips_noise_and_reference_sections(self) -> None:
        cleaned = clean_article_html(ARTICLE, title="Freedonia", host="en.wikipedia.org", source_url="u", revid=1)
        for gone in ("<img", "hatnote", "[1]", "[edit]", "Nav stuff", "Trailing reference", ">References<", "File:Flag"):
            assert gone not in cleaned.html, gone

    def test_text_view_flattens_tables_and_keeps_sentences_whole(self) -> None:
        text = clean_article_html(ARTICLE, title="Freedonia", host="en.wikipedia.org", source_url="u", revid=1).text
        assert text.startswith("Freedonia\n")
        assert "Year | GDP" in text and "1933 | 12" in text
        assert "Capital | Fredville" in text
        assert "Freedonia is ruled by Rufus T. Firefly and borders Sylvania." in text

    def test_filenames_are_unique_across_case(self) -> None:
        a = safe_filename("Red Dwarf", "https://en.wikipedia.org/wiki/Red_Dwarf")
        b = safe_filename("Red dwarf", "https://en.wikipedia.org/wiki/Red_dwarf")
        assert a.lower() != b.lower()
        assert "/" not in safe_filename("AC/DC", "https://en.wikipedia.org/wiki/AC/DC")


PAGES = {
    "Freedonia": '<p>Freedonia links to <a href="/wiki/Sylvania">Sylvania</a> and <a href="/wiki/Duck_Soup">Duck Soup</a>.</p>',
    "Rufus T. Firefly": "<p>Leader of Freedonia.</p>",
    "Sylvania": "<p>Rival nation.</p>",
    "Duck Soup": "<p>A 1933 film.</p>",
}


def _refs(*titles: str) -> list:
    return [f"https://en.wikipedia.org/wiki/{t.replace(' ', '_')}" for t in titles]


def _builder(source: FakeArticleSource, out: Path) -> CorpusBuilder:
    return CorpusBuilder(lambda _host: source, out, snapshot=FRAMES_SNAPSHOT, workers=2, harness_version="test")


class TestBuilder:
    def test_gold_tier_resolves_redirects_and_records_aliases(self, tmp_path: Path) -> None:
        source = FakeArticleSource(PAGES, redirects={"Freedonia (country)": "Freedonia"})
        manifest = _builder(source, tmp_path).build(
            _refs("Freedonia", "Freedonia (country)", "Rufus T. Firefly"), tier="G",
            distractor_count=0, seed=1, max_failed_gold_ratio=0.0,
        )
        assert sorted(d.title for d in manifest.documents) == ["Freedonia", "Rufus T. Firefly"]
        assert manifest.gold_aliases["en.wikipedia.org/Freedonia_(country)"] == "https://en.wikipedia.org/wiki/Freedonia"
        assert load_manifest(tmp_path).corpus_version == manifest.corpus_version

    def test_rebuild_resumes_without_refetching(self, tmp_path: Path) -> None:
        source = FakeArticleSource(PAGES)
        first = _builder(source, tmp_path).build(_refs("Freedonia", "Sylvania"), tier="G", distractor_count=0, seed=1, max_failed_gold_ratio=0.0)
        calls = len(source.parse_calls)
        second = _builder(source, tmp_path).build(_refs("Freedonia", "Sylvania"), tier="G", distractor_count=0, seed=1, max_failed_gold_ratio=0.0)
        assert len(source.parse_calls) == calls
        assert second.corpus_version == first.corpus_version

    def test_distractor_tier_samples_gold_outlinks(self, tmp_path: Path) -> None:
        manifest = _builder(FakeArticleSource(PAGES), tmp_path).build(
            _refs("Freedonia"), tier="GD", distractor_count=5, seed=1, max_failed_gold_ratio=0.0,
        )
        tiers = {d.title: d.tier for d in manifest.documents}
        assert tiers == {"Freedonia": "gold", "Sylvania": "distractor", "Duck Soup": "distractor"}

    def test_too_many_unresolved_gold_links_fail_the_build(self, tmp_path: Path) -> None:
        with pytest.raises(CorpusError):
            _builder(FakeArticleSource(PAGES), tmp_path).build(
                _refs("Freedonia", "Atlantis"), tier="G", distractor_count=0, seed=1, max_failed_gold_ratio=0.1,
            )
