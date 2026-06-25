"""Tests for Jira record identification helpers."""

from app.config.constants.arangodb import Connectors
from app.connectors.sources.atlassian.jira.enrichment.record_identifiers import (
    is_jira_connector,
    is_jira_ticket_record,
)
from app.models.entities import RecordType


class TestIsJiraConnector:
    def test_none_returns_false(self):
        assert is_jira_connector(None) is False

    def test_jira_variants(self):
        assert is_jira_connector(Connectors.JIRA) is True
        assert is_jira_connector(Connectors.JIRA_PERSONAL) is True
        assert is_jira_connector(Connectors.JIRA_DATA_CENTER) is True
        assert is_jira_connector(Connectors.JIRA_DATA_CENTER_PERSONAL) is True

    def test_non_jira_connector(self):
        assert is_jira_connector(Connectors.GOOGLE_DRIVE) is False
        assert is_jira_connector("not-a-connector") is False


class TestIsJiraTicketRecord:
    def test_none_or_empty_record(self):
        assert is_jira_ticket_record(None) is False
        assert is_jira_ticket_record({}) is False

    def test_jira_ticket(self):
        record = {
            "record_type": RecordType.TICKET.value,
            "connector_name": Connectors.JIRA.value,
        }
        assert is_jira_ticket_record(record) is True

    def test_jira_ticket_camel_case_keys(self):
        record = {
            "recordType": RecordType.TICKET.value,
            "connectorName": Connectors.JIRA_DATA_CENTER.value,
        }
        assert is_jira_ticket_record(record) is True

    def test_non_ticket_or_non_jira(self):
        assert is_jira_ticket_record({
            "record_type": RecordType.TICKET.value,
            "connector_name": Connectors.LINEAR.value,
        }) is False
        assert is_jira_ticket_record({
            "record_type": RecordType.FILE.value,
            "connector_name": Connectors.JIRA.value,
        }) is False
