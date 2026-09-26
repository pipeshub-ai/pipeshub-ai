"""A record whose link can't be opened keeps it out of the model's context.

Demo records keep a made-up address so search still returns them, and set
hideWeburl so the chat never offers it. Every path that rebuilds a record for
the model has to carry that flag, or the model sees the address and repeats it.
"""

from __future__ import annotations

import asyncio

import pytest

from app.utils import chat_helpers as ch

URL = "https://jira.acme-demo.example/browse/INC-2031"


def _graph_doc(hidden: bool) -> dict:
    return {
        "id": "rec-1",
        "recordName": "INC-2031: Synchronised billing retries",
        "recordType": "TICKET",
        "connectorName": "JIRA",
        "connectorId": "demo-connector",
        "externalRecordId": "INC-2031",
        "origin": "CONNECTOR",
        "webUrl": URL,
        "hideWeburl": hidden,
    }


def _record_dict(hidden: bool) -> dict:
    return {
        "id": "rec-1",
        "record_name": "INC-2031: Synchronised billing retries",
        "record_type": "TICKET",
        "connector_name": "JIRA",
        "origin": "CONNECTOR",
        "weburl": URL,
        "hide_weburl": hidden,
    }


@pytest.mark.parametrize("type_doc", [None, {"status": "Done", "priority": "High"}], ids=["base", "ticket"])
@pytest.mark.parametrize("hidden", [True, False])
def test_a_rebuilt_record_keeps_its_link_hidden(type_doc: dict | None, hidden: bool) -> None:
    record = ch.create_record_instance_from_dict(_record_dict(hidden), type_doc)
    assert record is not None
    assert record.weburl == URL  # still stored, so search keeps finding it
    assert (URL in record.to_llm_context(frontend_url=None)) is not hidden


@pytest.mark.parametrize("hidden", [True, False])
def test_graph_records_carry_the_flag_into_the_record_dict(hidden: bool) -> None:
    assert ch._build_record_dict_from_graph_base(_graph_doc(hidden))["hide_weburl"] is hidden
    merged = ch._merge_graph_into_blob_record({"id": "rec-1", "record_name": "INC-2031"}, _graph_doc(hidden))
    assert merged["hide_weburl"] is hidden


def test_a_missing_flag_reads_as_visible() -> None:
    doc = _graph_doc(False)
    del doc["hideWeburl"]
    assert ch._build_record_dict_from_graph_base(doc)["hide_weburl"] is False


@pytest.mark.parametrize("hidden", [True, False])
def test_the_graph_only_fallback_hides_the_link_too(hidden: bool) -> None:
    text = ch._base_record_context_metadata_from_graph(_graph_doc(hidden))
    assert "INC-2031" in text
    assert (URL in text) is not hidden


def test_linked_record_context_leaves_out_a_hidden_link() -> None:
    context = asyncio.run(
        ch._build_linked_record_context_metadata(
            "rec-1", graph_provider=None, doc_index={"rec-1": _graph_doc(True)}, frontend_url=None,
            type_doc={"status": "Done"},
        )
    )
    assert context and "INC-2031" in context
    assert URL not in context
