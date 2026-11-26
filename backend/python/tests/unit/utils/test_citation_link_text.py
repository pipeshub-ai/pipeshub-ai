"""P4.11 CR-1..CR-4: a citation's meaningful link text survives beside its number (P4.10 FB-5).

The model sometimes writes part of its sentence as a citation's link text: "the bug tracked as [NW-118](ref3)".
Replacing the link with its number alone left "tracked as [1]", and the user lost the ticket id they asked for."""

from __future__ import annotations

from typing import Any

import pytest

from app.utils.citations import (
    kept_citation_link_text,
    normalize_citations_and_chunks,
    normalize_citations_and_chunks_for_agent,
)

URL = "https://app.test/record/rec-nw/preview#blockIndex=0"
OTHER = "https://app.test/record/rec-nw/preview#blockIndex=1"


def _docs() -> list[dict[str, Any]]:
    meta = {"origin": "CONNECTOR", "recordName": "Tickets", "recordId": "rec-nw", "mimeType": "text/plain", "orgId": "o"}
    return [
        {"virtual_record_id": "rec-nw", "block_index": i, "block_type": "text", "content": f"block {i}",
         "block_web_url": url, "metadata": dict(meta)}
        for i, url in enumerate((URL, OTHER))
    ]


NORMALIZERS = [
    pytest.param(normalize_citations_and_chunks, id="chat"),
    pytest.param(normalize_citations_and_chunks_for_agent, id="agent"),
]


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("NW-118", "NW-118"),
        ("Leave Policy 2025", "Leave Policy 2025"),
        ("source", ""),
        ("Source", ""),
        (" sources ", ""),
        ("citation", ""),
        ("ref3", ""),
        ("3", ""),
        ("[3]", ""),
        ("", ""),
        ("   ", ""),
        ("https://ref3.xyz", ""),
        ("link", ""),
    ],
)
def test_cr1_cr2_a_generic_link_text_is_dropped_and_any_other_kept(text: str, label: str) -> None:
    assert kept_citation_link_text(text, "") == label


@pytest.mark.parametrize("normalize", NORMALIZERS)
def test_cr1_cr4_a_ticket_id_written_as_link_text_stays_beside_its_number(normalize: Any) -> None:  # noqa: ANN401
    text, citations = normalize("The crash is tracked as [NW-118](ref3).", _docs(), ref_to_url={"ref3": URL})
    assert text == f"The crash is tracked as NW-118 [1]({URL})."
    assert [c["chunkIndex"] for c in citations] == [1]


@pytest.mark.parametrize("normalize", NORMALIZERS)
def test_cr2_a_generic_link_text_becomes_the_number_as_before(normalize: Any) -> None:  # noqa: ANN401
    text, _ = normalize("Leave is 25 days [source](ref3) and more [2](ref4).", _docs(), ref_to_url={"ref3": URL, "ref4": OTHER})
    assert text == f"Leave is 25 days [1]({URL}) and more [2]({OTHER})."


@pytest.mark.parametrize("normalize", NORMALIZERS)
def test_cr1_two_labelled_links_side_by_side_stay_apart(normalize: Any) -> None:  # noqa: ANN401
    text, _ = normalize("Open: [NW-112](ref3)[NW-114](ref4).", _docs(), ref_to_url={"ref3": URL, "ref4": OTHER})
    assert text == f"Open: NW-112 [1]({URL}) NW-114 [2]({OTHER})."


@pytest.mark.parametrize("normalize", NORMALIZERS)
def test_cr3_an_unresolved_citation_keeps_its_meaningful_text(normalize: Any) -> None:  # noqa: ANN401
    text, citations = normalize("Lena owns [NW-112](ref9) and [source](ref8).", _docs(), ref_to_url={})
    assert text == "Lena owns NW-112 and ."
    assert citations == []
