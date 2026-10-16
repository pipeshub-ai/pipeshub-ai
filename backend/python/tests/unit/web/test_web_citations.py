"""Web citations name the page, and a reference the model made up never reaches the answer."""

import pytest

from app.utils.citations import (
    normalize_citations_and_chunks,
    normalize_citations_and_chunks_for_agent,
)

PAGE = "https://news.example.com/fed"
FRAGMENT = f"{PAGE}#:~:text=held"


def web_record(title: str = "Fed holds rates") -> dict:
    return {"url": FRAGMENT, "title": title, "content": "The Fed held.", "source_type": "web", "org_id": "org-1"}


def normalize(normalizer, text: str, records: list[dict]) -> tuple[str, list[dict]]:
    if normalizer is normalize_citations_and_chunks_for_agent:
        return normalizer(text, [], virtual_record_id_to_result={}, records=[], ref_to_url={"ref1": FRAGMENT}, web_records=records)
    return normalizer(text, [], records=[], ref_to_url={"ref1": FRAGMENT}, web_records=records)


NORMALIZERS = [normalize_citations_and_chunks, normalize_citations_and_chunks_for_agent]


@pytest.mark.parametrize("normalizer", NORMALIZERS)
def test_the_citation_is_named_after_the_page_title(normalizer) -> None:
    _, citations = normalize(normalizer, "Rates held [source](https://ref1.xyz).", [web_record()])
    assert [c["metadata"]["recordName"] for c in citations] == ["Fed holds rates"]
    assert citations[0]["metadata"]["webUrl"] == FRAGMENT
    assert citations[0]["metadata"]["recordId"] == PAGE


@pytest.mark.parametrize("normalizer", NORMALIZERS)
def test_without_a_title_the_site_names_it(normalizer) -> None:
    _, citations = normalize(normalizer, "Rates held [source](https://ref1.xyz).", [web_record(title="")])
    assert citations[0]["metadata"]["recordName"] == "news.example.com"


@pytest.mark.parametrize("normalizer", NORMALIZERS)
def test_invented_references_are_removed(normalizer) -> None:
    text, citations = normalize(
        normalizer,
        "Rates held [source](https://ref1.xyz). Also [source](https://ref9.xyz) and [x](ref7) and [y](http://ref12.xyz/).",
        [web_record()],
    )
    assert "ref9" not in text and "ref7" not in text and "ref12" not in text
    assert "(https://news.example.com/fed#:~:text=held)" in text
    assert len(citations) == 1


@pytest.mark.parametrize("normalizer", NORMALIZERS)
def test_ordinary_links_are_kept(normalizer) -> None:
    text, _ = normalize(normalizer, "See [the docs](https://docs.example.com/) and [ref](https://ref.example.xyz/a).", [web_record()])
    assert "[the docs](https://docs.example.com/)" in text
    assert "[ref](https://ref.example.xyz/a)" in text


@pytest.mark.parametrize("normalizer", NORMALIZERS)
def test_an_invented_link_keeps_descriptive_text(normalizer) -> None:
    text, _ = normalize(normalizer, "See [India – Wikipedia](https://ref42.xyz) and [source](https://ref43.xyz) and [7](https://ref44.xyz).", [web_record()])
    assert text == "See India – Wikipedia and  and ."
