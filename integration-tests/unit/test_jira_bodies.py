"""The Jira suites' setup bodies: what each names, and that Jira can display what it embeds."""

from __future__ import annotations

from typing import Any

import pytest
from connectors.jira.jira_bodies import media_adf, wiki_markup

pytestmark = pytest.mark.unit


def _media(doc: dict[str, Any], parent: str) -> list[dict[str, Any]]:
    return [m["attrs"] for n in doc["content"] if n["type"] == parent for m in n["content"]]


def test_the_adf_body_names_each_attachment_by_alt() -> None:
    doc = media_adf("the chart", images=("chart.png",), files=("report.txt", "notes.txt"))
    assert doc["content"][0]["content"][0]["text"] == "the chart"
    assert [m["alt"] for m in _media(doc, "mediaSingle")] == ["chart.png"]
    assert [m["alt"] for m in _media(doc, "mediaGroup")] == ["report.txt", "notes.txt"]


def test_an_inline_image_has_the_dimensions_jira_needs_to_show_it() -> None:
    (image,) = _media(media_adf("x", images=("chart.png",)), "mediaSingle")
    assert image["width"] > 0 and image["height"] > 0


def test_no_media_nodes_without_attachments() -> None:
    assert [n["type"] for n in media_adf("plain")["content"]] == ["paragraph"]


def test_the_wiki_body_names_the_same_attachments() -> None:
    assert wiki_markup("the chart", images=("chart.png",), files=("report.txt",)) == (
        "the chart\n\n!chart.png!\n\n[^report.txt]")
