"""Shared fixtures for localKB KnowledgeBaseService unit tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_graph_provider():
    provider = AsyncMock()
    # Like the real providers: a list, empty when nothing was uploaded.
    provider.get_uploaded_document_ids = AsyncMock(return_value=[])
    # The folder-depth checks compare these with a number; a bare mock can't be compared.
    provider.get_folder_depth = AsyncMock(return_value=1)
    provider.get_folder_subtree_height = AsyncMock(return_value=0)
    return provider


@pytest.fixture
def mock_kafka_service():
    return AsyncMock()


@pytest.fixture
def mock_processor():
    proc = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_record_metadata_update = AsyncMock()
    proc.on_record_content_update = AsyncMock()
    proc.on_records_deleted_cascade = AsyncMock(
        return_value={"success": True, "successfully_deleted": 1, "total_requested": 1}
    )
    proc.on_records_moved = AsyncMock()
    return proc


def make_mock_file_record(**kwargs):
    rec = MagicMock()
    rec.external_record_id = kwargs.get("external_record_id", "ext-rec1")
    rec.record_name = kwargs.get("record_name", "myfile.pdf")
    rec.parent_external_record_id = kwargs.get("parent_external_record_id")
    return rec


@pytest.fixture
def mock_config_service():
    return AsyncMock()


@pytest.fixture
def service(mock_logger, mock_graph_provider, mock_kafka_service, mock_processor, mock_config_service):
    return KnowledgeBaseService(
        mock_logger,
        mock_graph_provider,
        mock_kafka_service,
        processor=mock_processor,
        config_service=mock_config_service,
    )
