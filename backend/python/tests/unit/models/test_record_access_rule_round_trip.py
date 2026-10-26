"""Every typed record reads its stored access rule back.

A converter that skipped ``accessRule`` loaded every stored record as OPEN. Code that then
compares the loaded rule, or saves the record back (``to_arango_base_record`` writes it),
treats an item restricted at its source as open, as Confluence pages were.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config.constants.arangodb import AccessRule, Connectors, OriginTypes
from app.models import entities as E

_TYPED = [
    (E.MessageRecord, E.RecordType.MESSAGE, {}),
    (E.MailRecord, E.RecordType.MAIL, {}),
    (E.LinkRecord, E.RecordType.LINK, {"url": "https://example.com", "is_public": E.LinkPublicStatus.FALSE}),
    (E.CommentRecord, E.RecordType.COMMENT, {"author_source_id": "author-1"}),
    (E.TicketRecord, E.RecordType.TICKET, {}),
    (E.ProjectRecord, E.RecordType.PROJECT, {}),
    (E.ProductRecord, E.RecordType.PRODUCT, {}),
    (E.DealRecord, E.RecordType.DEAL, {}),
    (E.SQLViewRecord, E.RecordType.SQL_VIEW, {}),
    (E.SQLTableRecord, E.RecordType.SQL_TABLE, {}),
    (E.SQLColumnRecord, E.RecordType.SQL_COLUMN, {}),
    (E.PullRequestRecord, E.RecordType.PULL_REQUEST, {}),
    (E.ArtifactRecord, E.RecordType.ARTIFACT, {}),
    (E.CodeFileRecord, E.RecordType.CODE_FILE, {}),
    (E.MeetingRecord, E.RecordType.MEETING, {}),
    (E.WebpageRecord, E.RecordType.CONFLUENCE_PAGE, {}),
]
_IDS = [cls.__name__ for cls, _, _ in _TYPED]


def _stored(cls: type[E.Record], record_type: E.RecordType, extra: dict[str, Any]) -> tuple[dict, dict]:
    """The record and type documents a RESTRICTED record of ``cls`` is stored as."""
    record = cls(
        id="rec-1", org_id="org-1", record_name="item", record_type=record_type, external_record_id="ext-1",
        version=0, origin=OriginTypes.CONNECTOR, connector_name=Connectors.JIRA, connector_id="conn-1",
        access_rule=AccessRule.RESTRICTED, **extra,
    )
    to_type_doc = getattr(record, "to_arango_record", None) or record.to_arango_artifact_record
    return to_type_doc(), record.to_arango_base_record()


@pytest.mark.parametrize(("cls", "record_type", "extra"), _TYPED, ids=_IDS)
def test_a_stored_restriction_loads_back(cls: type[E.Record], record_type: E.RecordType, extra: dict[str, Any]) -> None:
    type_doc, record_doc = _stored(cls, record_type, extra)
    assert record_doc["accessRule"] == AccessRule.RESTRICTED.value
    assert cls.from_arango_record(type_doc, record_doc).access_rule == AccessRule.RESTRICTED


@pytest.mark.parametrize(("cls", "record_type", "extra"), _TYPED, ids=_IDS)
def test_a_record_stored_without_a_rule_loads_open(
    cls: type[E.Record], record_type: E.RecordType, extra: dict[str, Any]
) -> None:
    type_doc, record_doc = _stored(cls, record_type, extra)
    del record_doc["accessRule"]
    assert cls.from_arango_record(type_doc, record_doc).access_rule == AccessRule.OPEN


@pytest.mark.parametrize(("cls", "record_type", "extra"), _TYPED, ids=_IDS)
def test_an_unrecognised_rule_loads_restricted(
    cls: type[E.Record], record_type: E.RecordType, extra: dict[str, Any]
) -> None:
    type_doc, record_doc = _stored(cls, record_type, extra)
    record_doc["accessRule"] = "PUBLIC_TO_ALL"
    assert cls.from_arango_record(type_doc, record_doc).access_rule == AccessRule.RESTRICTED
