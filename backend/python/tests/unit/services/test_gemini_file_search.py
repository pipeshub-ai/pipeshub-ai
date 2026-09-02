from types import SimpleNamespace
from unittest.mock import Mock

from app.services.gemini_file_search import GeminiFileSearchService


def _service() -> GeminiFileSearchService:
    service = GeminiFileSearchService(Mock(), None)
    service.enabled = True
    service._api_key = "test-key"
    return service


def test_escape_filter_value_quotes_and_backslashes() -> None:
    assert GeminiFileSearchService.escape_filter_value('a\\b"c') == 'a\\\\b\\"c'


def test_document_metadata_value_supports_sdk_and_dict_shapes() -> None:
    sdk_doc = SimpleNamespace(
        custom_metadata=[SimpleNamespace(key="recordId", string_value="record-1")]
    )
    dict_doc = SimpleNamespace(
        custom_metadata=[{"key": "recordId", "stringValue": "record-2"}]
    )

    assert (
        GeminiFileSearchService._document_metadata_value(sdk_doc, "recordId")
        == "record-1"
    )
    assert (
        GeminiFileSearchService._document_metadata_value(dict_doc, "recordId")
        == "record-2"
    )


def test_existing_documents_are_matched_by_record_id_not_display_name() -> None:
    matching = SimpleNamespace(
        name="stores/1/documents/1",
        display_name="duplicate.pdf",
        custom_metadata=[SimpleNamespace(key="recordId", string_value="record-1")],
    )
    same_name_other_record = SimpleNamespace(
        name="stores/1/documents/2",
        display_name="duplicate.pdf",
        custom_metadata=[SimpleNamespace(key="recordId", string_value="record-2")],
    )
    documents = Mock()
    documents.list.return_value = [matching, same_name_other_record]
    client = SimpleNamespace(file_search_stores=SimpleNamespace(documents=documents))

    assert _service()._find_documents_by_record_id(client, "stores/1", "record-1") == [
        matching
    ]


def test_bad_media_blob_does_not_abort_other_downloads() -> None:
    stores = Mock()
    stores.download_media.side_effect = [object(), b"valid"]
    client = SimpleNamespace(file_search_stores=stores)
    service = _service()
    service.max_media_per_query = 2

    result = service._download_cited_media(
        client,
        [{"mediaId": "bad"}, {"mediaId": "good"}],
    )

    assert "bad" not in result
    assert result["good"]["dataUri"].startswith("data:image/png;base64,")
