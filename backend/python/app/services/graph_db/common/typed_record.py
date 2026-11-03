"""Build the typed ``Record`` (FileRecord, TicketRecord, ...) from a record document and its type document.

Used by the PostgreSQL graph provider, which needs no other provider's driver to read a record; the Neo4j provider keeps its own factory (``_create_typed_record_from_neo4j``) until it is switched over.
"""
from __future__ import annotations

from app.config.constants.arangodb import (
    RECORD_TYPE_COLLECTION_MAPPING,
    CollectionNames,
)
from app.models.entities import (
    ArtifactRecord,
    CodeFileRecord,
    CommentRecord,
    DealRecord,
    FileRecord,
    LinkRecord,
    MailRecord,
    MeetingRecord,
    MessageRecord,
    ProductRecord,
    ProjectRecord,
    PullRequestRecord,
    Record,
    SQLColumnRecord,
    SQLTableRecord,
    SQLViewRecord,
    TicketRecord,
    WebpageRecord,
)

_BY_COLLECTION: dict[str, type[Record]] = {
    CollectionNames.FILES.value: FileRecord,
    CollectionNames.MAILS.value: MailRecord,
    CollectionNames.MESSAGES.value: MessageRecord,
    CollectionNames.WEBPAGES.value: WebpageRecord,
    CollectionNames.TICKETS.value: TicketRecord,
    CollectionNames.PROJECTS.value: ProjectRecord,
    CollectionNames.COMMENTS.value: CommentRecord,
    CollectionNames.LINKS.value: LinkRecord,
    CollectionNames.MEETINGS.value: MeetingRecord,
    CollectionNames.PRODUCTS.value: ProductRecord,
    CollectionNames.DEALS.value: DealRecord,
    CollectionNames.ARTIFACTS.value: ArtifactRecord,
    CollectionNames.SQL_TABLES.value: SQLTableRecord,
    CollectionNames.SQL_COLUMNS.value: SQLColumnRecord,
    CollectionNames.SQL_VIEWS.value: SQLViewRecord,
    CollectionNames.CODE_FILES.value: CodeFileRecord,
    CollectionNames.PULLREQUESTS.value: PullRequestRecord,
}


def typed_record(record_dict: dict, type_doc: dict | None) -> Record:
    """The typed record of ``record_dict`` (Arango form). Raises ValueError when the type has no type collection, no type document, or will not build."""
    record_type = record_dict.get("recordType")
    if not type_doc or record_type not in RECORD_TYPE_COLLECTION_MAPPING:
        raise ValueError(f"No type collection or no type doc, record type:{record_type} or type doc:{type_doc}")
    cls = _BY_COLLECTION.get(RECORD_TYPE_COLLECTION_MAPPING[record_type])
    if cls is None:
        raise ValueError(f"Invalid record type: {record_type}")
    try:
        return cls.from_arango_record(type_doc, record_dict)
    except Exception as e:
        raise ValueError(f"Failed to create typed record for {record_type}") from e
