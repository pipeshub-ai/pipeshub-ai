"""Anti-false-positive corpus: strict validation must accept what the app really writes.

Every payload here is built by the application's own converters
(``to_arango_*`` in ``app.models.entities``), never hand-written, and then
checked with ``NodeSchemaValidator().validate_node`` (full: type, enum,
required, additionalProperties). Records are checked as the connector path
writes them: the base document to ``records`` plus the subtype document to the
collection ``RECORD_TYPE_COLLECTION_MAPPING`` names.

Instances come from a small introspection-driven filler (``_fill``) in eight
modes: minimal (required only), full, nulls (every optional field None),
empty strings, unicode, very long values, zero numbers, negative numbers.

Three shapes are checked:
  * "as stored": the converter output with ``_key`` renamed to ``id``, which is
    what a graph-DB node looks like after ``_arango_to_neo4j_node``.
  * "as written": the raw converter output, ``_key`` included. See
    ``test_converter_output_with_arango_key_is_accepted``.
  * Neo4j round trip: raw doc -> ``_arango_to_neo4j_node`` -> (Neo4j drops
    nulls) -> ``_neo4j_to_arango_node`` -> validate; nested values must come
    back equal.

A red test here is a real finding: a payload the app writes that the schema
rejects. Do not weaken it; loosen the schema or fix the writer.
"""

from __future__ import annotations

import inspect
import types
import typing
from enum import Enum
from unittest.mock import MagicMock

import pytest
from jsonschema import validators
from jsonschema.exceptions import best_match

from app.config.constants.arangodb import (
    RECORD_TYPE_COLLECTION_MAPPING,
    AccessRule,
    CollectionNames,
    Connectors,
    DeleteSource,
    OriginTypes,
    PermissionModel,
    ProgressStatus,
)
from app.models import entities as E
from app.models.entities import RecordType
from app.schema.arango import documents as D
from app.schema.node_schema_registry import (
    JSON_ENCODED_FIELDS,
    adapt_schema,
    get_node_schema,
)
from app.schema.node_validator import NodeSchemaValidator
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

_VALIDATOR = NodeSchemaValidator()

# Collections the Arango side validates but the registry does not map yet.
# Checked straight against the Arango rule so these cases are not vacuous.
_UNREGISTERED_RULES = {
    CollectionNames.PULLREQUESTS.value: D.pull_request_record_schema,
    CollectionNames.PRODUCTS.value: D.product_record_schema,
    CollectionNames.DEALS.value: D.deal_record_schema,
    CollectionNames.CODE_FILES.value: D.code_file_record_schema,
    CollectionNames.SQL_TABLES.value: D.sql_table_record_schema,
    CollectionNames.SQL_COLUMNS.value: D.sql_column_record_schema,
    CollectionNames.SQL_VIEWS.value: D.sql_view_record_schema,
    CollectionNames.BLOCKS.value: D.block_schema,
}


def _validate(collection: str, doc: dict) -> None:
    """Full validation through the production validator, plus the Arango rule when unmapped."""
    _VALIDATOR.validate_node(collection, doc)
    if get_node_schema(collection) is None and collection in _UNREGISTERED_RULES:
        schema = adapt_schema(_UNREGISTERED_RULES[collection])
        body = {k: v for k, v in doc.items() if k != "_id"}
        error = best_match(validators.validator_for(schema)(schema).iter_errors(body))
        assert error is None, (
            f"{collection} (Arango rule, not yet in the registry): "
            f"at '{'.'.join(map(str, error.path)) or 'root'}': {error.message}"
        )


def _stored(doc: dict) -> dict:
    """The node as the graph store holds it: ``_key`` is ``id``."""
    out = dict(doc)
    if "_key" in out:
        out["id"] = out.pop("_key")
    return out


# ---------------------------------------------------------------------------
# Introspection-driven filler
# ---------------------------------------------------------------------------
MODES = ["minimal", "full", "nulls", "empty", "unicode", "long", "zero", "negative"]
_TEXT = {"unicode": "naïve 日本語 🚀 שלום é", "long": "x" * 50_000}
_NOW = 1_700_000_000_000


def _members(ann):
    if typing.get_origin(ann) in (typing.Union, types.UnionType):
        return [a for a in typing.get_args(ann) if a is not type(None)]
    return [ann]


def _nullable(ann) -> bool:
    return type(None) in typing.get_args(ann)


def _fill(ann, mode: str, name: str):
    cands = _members(ann)
    pick = cands[0]
    if str in cands and mode in ("empty", "unicode", "long"):
        pick = str
    origin = typing.get_origin(pick) or pick
    if isinstance(origin, type) and issubclass(origin, Enum):
        return list(origin)[0]
    if origin is bool:
        return mode == "full"
    if origin is int:
        return {"zero": 0, "negative": -1}.get(mode, _NOW)
    if origin is float:
        return {"zero": 0.0, "negative": -1.5}.get(mode, 12.5)
    if origin is str:
        return {"empty": "", "unicode": _TEXT["unicode"], "long": _TEXT["long"]}.get(mode, f"{name}-value")
    if origin is list:
        args = typing.get_args(pick)
        inner = args[0] if args else str
        if mode == "empty":
            return []
        if (typing.get_origin(inner) or inner) in (dict, typing.Dict):
            return [{"column": "a", "ref": {"table": "t", "column": "id"}}]
        if inner is float:
            return [1.5, 2.5]
        return [_fill(inner, mode if mode in ("unicode", "long") else "full", name)] * (2 if mode != "long" else 3)
    if origin is dict:
        return {"type": "object", "properties": {"a": {"type": "string", "enum": ["x", None]}}}
    raise AssertionError(f"filler cannot handle {name}: {ann}")


# Base Record fields that carry a scalar the converter persists. Status-like
# strings and the timestamp-origin pattern are fixed values, not free text.
_BASE_FILL = (
    "org_id version created_at updated_at external_revision_id external_record_group_id record_group_id "
    "root_record_group_id parent_external_record_id virtual_record_id md5_hash size_in_bytes mime_type weburl "
    "source_created_at source_updated_at processing_started_at queued_at reason uploaded_by storage_document_id "
    "deleted_at deleted_by_user_id delete_batch_id purge_attempts purge_last_error trashed_external_record_id "
    "preview_renderable is_shared hide_weburl is_internal is_placeholder is_dependent_node parent_node_id "
    "is_vlm_ocr_processed is_deleted push_origin delete_source"
).split()

_ACCOUNT_TYPES = D.orgs_schema["rule"]["properties"]["accountType"]["enum"]

# Values a writer is documented to send that a free-text filler would not produce.
_OVERRIDES: dict[type, dict[str, object]] = {
    E.RecordGroup: {"permission_model": PermissionModel.RECORD_GROUP_LEVEL},
    E.ArtifactsRecordGroup: {"permission_model": PermissionModel.RECORD_GROUP_LEVEL},
    E.Org: {"account_type": _ACCOUNT_TYPES[0]},  # an enum in the schema, free text on the model
    # column_records.py always sets both; the schema requires them non-empty.
    E.SQLColumnRecord: {"fqn": "db.public.t.c", "table_fqn": "db.public.t"},
    # Every code-file writer sets the path. The hash is NOT overridden: it can be None (sha: str | None).
    E.CodeFileRecord: {"file_path": "src/app.py"},
}


def _record_kwargs(cls, record_type: RecordType, mode: str) -> dict:
    text_mode = mode in ("unicode", "long")
    kw = dict(
        record_name=_TEXT[mode] if text_mode else "Quarterly report.pdf",
        record_type=record_type,
        external_record_id=_TEXT[mode] if text_mode else "ext-1",
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_DRIVE,
        connector_id="conn-1",
        org_id="org-1",
    )
    base_fields = E.Record.model_fields
    if mode != "minimal":
        for name in _BASE_FILL:
            ann = base_fields[name].annotation
            if mode == "nulls":
                # None here is its own test (test_null_vlm_ocr_flag_is_accepted), not 22 copies of one failure.
                if _nullable(ann) and name != "is_vlm_ocr_processed":
                    kw[name] = None
            else:
                kw[name] = _fill(ann, mode, name)
        if mode == "full":
            kw["source_timestamp_origin"] = "upload"
            kw["push_origin"] = True
            kw["access_rule"] = AccessRule.STRICT
    for name, field in cls.model_fields.items():
        if name in base_fields:
            continue
        if field.is_required():
            kw[name] = _fill(field.annotation, "full" if mode in ("minimal", "nulls") else mode, name)
        elif mode == "nulls":
            if _nullable(field.annotation):
                kw[name] = None
        elif mode != "minimal":
            kw[name] = _fill(field.annotation, mode, name)
    kw.update(_OVERRIDES.get(cls, {}))
    return kw


def _typed_doc(record) -> dict | None:
    conv = getattr(record, "to_arango_record", None) or getattr(record, "to_arango_artifact_record", None)
    return conv() if conv else None


# class -> record types it is used for (the typed collection comes from RECORD_TYPE_COLLECTION_MAPPING)
_RT = RecordType
_RECORD_CLASSES: dict[type, list[RecordType]] = {
    E.FileRecord: [_RT.FILE],
    E.MessageRecord: [_RT.MESSAGE],
    E.MailRecord: [_RT.MAIL, _RT.GROUP_MAIL],
    E.WebpageRecord: [_RT.WEBPAGE, _RT.CONFLUENCE_PAGE, _RT.CONFLUENCE_BLOGPOST, _RT.DATABASE, _RT.DATASOURCE],
    E.SharePointPageRecord: [_RT.SHAREPOINT_PAGE],
    E.LinkRecord: [_RT.LINK],
    E.CommentRecord: [_RT.COMMENT, _RT.INLINE_COMMENT],
    E.TicketRecord: [_RT.TICKET, _RT.CASE, _RT.TASK],
    E.ProjectRecord: [_RT.PROJECT],
    E.ProductRecord: [_RT.PRODUCT],
    E.DealRecord: [_RT.DEAL],
    E.MeetingRecord: [_RT.MEETING],
    E.ArtifactRecord: [_RT.ARTIFACT],
    E.CodeFileRecord: [_RT.CODE_FILE],
    E.PullRequestRecord: [_RT.PULL_REQUEST],
    E.SQLTableRecord: [_RT.SQL_TABLE],
    E.SQLColumnRecord: [_RT.SQL_COLUMN],
    E.SQLViewRecord: [_RT.SQL_VIEW],
    # base document only (no typed collection)
    E.SharePointListRecord: [_RT.SHAREPOINT_LIST],
    E.SharePointListItemRecord: [_RT.SHAREPOINT_LIST_ITEM],
    E.SharePointDocumentLibraryRecord: [_RT.SHAREPOINT_DOCUMENT_LIBRARY],
    E.Record: [_RT.DRIVE, _RT.OTHERS],
}
_RECORD_CASES = [
    pytest.param(cls, rt, id=f"{cls.__name__}-{rt.value}") for cls, rts in _RECORD_CLASSES.items() for rt in rts
]


def _typed_collection(rt: RecordType) -> str | None:
    return RECORD_TYPE_COLLECTION_MAPPING.get(rt.value)


def _record_docs(cls, rt, mode) -> list[tuple[str, dict]]:
    """(collection, doc) pairs the connector path writes for one record."""
    rec = cls(**_record_kwargs(cls, rt, mode))
    docs = [(CollectionNames.RECORDS.value, rec.to_arango_base_record())]
    typed, collection = _typed_doc(rec), _typed_collection(rt)
    if typed is not None and collection:
        docs.append((collection, typed))
    return docs


def _has_typed_doc(cls, rt) -> bool:
    return bool(_typed_collection(rt)) and (
        hasattr(cls, "to_arango_record") or hasattr(cls, "to_arango_artifact_record")
    )


# One case per document the connector path writes, so a rejected subtype doc is not hidden behind the base doc.
_DOC_MODE_CASES = [
    pytest.param(cls, rt, mode, part, id=f"{cls.__name__}-{rt.value}-{mode}-{part}")
    for cls, rts in _RECORD_CLASSES.items()
    for rt in rts
    for mode in MODES
    for part in (("base", "typed") if _has_typed_doc(cls, rt) else ("base",))
]


def _part(cls, rt, mode, part) -> tuple[str, dict]:
    docs = _record_docs(cls, rt, mode)
    return docs[0] if part == "base" else docs[1]


# ---------------------------------------------------------------------------
# Non-record entities: (class, converter name, collection, required kwargs)
# ---------------------------------------------------------------------------
_RG_REQ = dict(
    name="Engineering",
    external_group_id="grp-1",
    connector_name=Connectors.CONFLUENCE,
    connector_id="conn-1",
    group_type=E.RecordGroupType.CONFLUENCE_SPACES,
)
_ENTITIES = [
    (E.RecordGroup, "to_arango_base_record_group", CollectionNames.RECORD_GROUPS.value, _RG_REQ),
    (E.ArtifactsRecordGroup, "to_arango_base_record_group", CollectionNames.RECORD_GROUPS.value, _RG_REQ),
    (E.Org, "to_arango_org", CollectionNames.ORGS.value, dict(name="Acme")),
    (E.Person, "to_arango_person", CollectionNames.PEOPLE.value, dict(email="Ann@Example.com")),
    (
        E.AppUser,
        "to_arango_base_user",
        CollectionNames.USERS.value,
        dict(app_name=Connectors.SLACK, connector_id="c", source_user_id="U1", email="a@b.co", full_name="Ann"),
    ),
    (
        E.AppUserGroup,
        "to_arango_base_user_group",
        CollectionNames.GROUPS.value,
        dict(app_name=Connectors.SLACK, connector_id="c", source_user_group_id="G1", name="eng"),
    ),
    (
        E.AppRole,
        "to_arango_base_role",
        CollectionNames.ROLES.value,
        dict(app_name=Connectors.SLACK, connector_id="c", source_role_id="R1", name="admin"),
    ),
]
_ENTITY_MODE_CASES = [
    pytest.param(cls, conv, col, req, mode, id=f"{cls.__name__}-{mode}")
    for cls, conv, col, req in _ENTITIES
    for mode in MODES
]


def _entity(cls, req, mode):
    kw = dict(req)
    if mode != "minimal":
        for name, field in cls.model_fields.items():
            if name in req:
                continue
            if mode == "nulls":
                if _nullable(field.annotation):
                    kw[name] = None
            else:
                kw[name] = _fill(field.annotation, mode, name)
    kw.update(_OVERRIDES.get(cls, {}))
    if "email" in req and mode in ("empty",):
        kw["email"] = "a@b.co"  # an address is never empty; Person lowercases it
    return cls(**kw)


# ---------------------------------------------------------------------------
# Corpus: as stored
# ---------------------------------------------------------------------------
class TestRecordPayloads:
    @pytest.mark.parametrize(("cls", "rt", "mode", "part"), _DOC_MODE_CASES)
    def test_connector_path_docs_validate(self, cls, rt, mode, part):
        collection, doc = _part(cls, rt, mode, part)
        _validate(collection, _stored(doc))

    def test_null_vlm_ocr_flag_is_accepted(self):
        """``Record.is_vlm_ocr_processed`` is ``bool | None`` and the converter writes it as is
        (entities.py to_arango_base_record, ``isVLMOcrProcessed``); the schema says boolean only."""
        kw = _record_kwargs(E.Record, RecordType.FILE, "minimal") | {"is_vlm_ocr_processed": None}
        _validate(CollectionNames.RECORDS.value, _stored(E.Record(**kw).to_arango_base_record()))

    @pytest.mark.parametrize("account_type", _ACCOUNT_TYPES)
    def test_org_account_types(self, account_type):
        _validate(CollectionNames.ORGS.value, _stored(E.Org(name="o", account_type=account_type).to_arango_org()))

    @pytest.mark.parametrize(("cls", "rt"), _RECORD_CASES)
    def test_typed_collection_matches_mapping(self, cls, rt):
        """The pair has a subtype doc exactly when the mapping names a collection (guards the corpus itself)."""
        assert (len(_record_docs(cls, rt, "minimal")) == 2) == _has_typed_doc(cls, rt)

    @pytest.mark.parametrize("rt", list(RecordType), ids=lambda r: r.value)
    def test_every_record_type_base_doc(self, rt):
        doc = E.Record(**_record_kwargs(E.Record, rt, "full")).to_arango_base_record()
        _validate(CollectionNames.RECORDS.value, _stored(doc))

    @pytest.mark.parametrize("origin", list(OriginTypes), ids=lambda o: o.value)
    def test_every_origin(self, origin):
        kw = _record_kwargs(E.Record, RecordType.FILE, "minimal") | {"origin": origin}
        _validate(CollectionNames.RECORDS.value, _stored(E.Record(**kw).to_arango_base_record()))

    @pytest.mark.parametrize("connector", list(Connectors), ids=lambda c: c.name)
    def test_every_connector_name(self, connector):
        kw = _record_kwargs(E.Record, RecordType.FILE, "minimal") | {"connector_name": connector}
        _validate(CollectionNames.RECORDS.value, _stored(E.Record(**kw).to_arango_base_record()))

    @pytest.mark.parametrize("status", list(ProgressStatus), ids=lambda s: s.name)
    @pytest.mark.parametrize("field", ["parsing_status", "indexing_status", "extraction_status"])
    def test_every_progress_status(self, field, status):
        kw = _record_kwargs(E.Record, RecordType.FILE, "minimal") | {field: status.value}
        _validate(CollectionNames.RECORDS.value, _stored(E.Record(**kw).to_arango_base_record()))

    @pytest.mark.parametrize("rule", list(AccessRule), ids=lambda r: r.name)
    def test_every_access_rule(self, rule):
        kw = _record_kwargs(E.Record, RecordType.FILE, "minimal") | {"access_rule": rule}
        _validate(CollectionNames.RECORDS.value, _stored(E.Record(**kw).to_arango_base_record()))

    @pytest.mark.parametrize("source", [*DeleteSource, None], ids=lambda s: getattr(s, "name", "none"))
    def test_every_delete_source(self, source):
        kw = _record_kwargs(E.Record, RecordType.FILE, "minimal") | {
            "is_deleted": source is not None,
            "deleted_at": _NOW if source else None,
            "delete_source": source,
        }
        _validate(CollectionNames.RECORDS.value, _stored(E.Record(**kw).to_arango_base_record()))

    @pytest.mark.parametrize("status", list(E.LinkPublicStatus), ids=lambda s: s.name)
    def test_every_link_public_status(self, status):
        kw = _record_kwargs(E.LinkRecord, RecordType.LINK, "minimal") | {"is_public": status}
        _validate(CollectionNames.LINKS.value, _stored(E.LinkRecord(**kw).to_arango_record()))

    @pytest.mark.parametrize(
        ("field", "member"),
        [(f, m) for f, enum in [("status", E.Status), ("priority", E.Priority), ("type", E.ItemType),
                                ("delivery_status", E.DeliveryStatus)] for m in enum],
        ids=lambda v: getattr(v, "name", v),
    )
    def test_every_ticket_enum_member(self, field, member):
        kw = _record_kwargs(E.TicketRecord, RecordType.TICKET, "minimal") | {field: member}
        _validate(CollectionNames.TICKETS.value, _stored(E.TicketRecord(**kw).to_arango_record()))

    @pytest.mark.parametrize(
        ("field", "member"),
        [(f, m) for f, enum in [("lifecycle_status", E.LifecycleStatus), ("artifact_type", E.ArtifactType),
                                ("visibility", E.ArtifactVisibility)] for m in enum],
        ids=lambda v: getattr(v, "name", v),
    )
    def test_every_artifact_enum_member(self, field, member):
        kw = _record_kwargs(E.ArtifactRecord, RecordType.ARTIFACT, "minimal") | {field: member}
        _validate(CollectionNames.ARTIFACTS.value, _stored(E.ArtifactRecord(**kw).to_arango_artifact_record()))

    def test_artifact_with_versions_and_result_schema(self):
        kw = _record_kwargs(E.ArtifactRecord, RecordType.ARTIFACT, "minimal") | {
            "versions": [{"registryVersion": 1, "storageVersion": 0, "contentHash": "h", "sizeBytes": 3, "createdAt": 5}],
            "result_schema": {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "object"}}}},
        }
        doc = E.ArtifactRecord(**kw).to_arango_artifact_record()
        _validate(CollectionNames.ARTIFACTS.value, _stored(doc))

    def test_sql_table_with_foreign_keys(self):
        kw = _record_kwargs(E.SQLTableRecord, RecordType.SQL_TABLE, "minimal") | {
            "foreign_keys": [{"column": "user_id", "ref": {"table": "users", "column": "id"}}],
            "primary_keys": ["id"],
        }
        _validate(CollectionNames.SQL_TABLES.value, _stored(E.SQLTableRecord(**kw).to_arango_record()))

    def test_neo4j_readback_with_record_properties_and_internal_keys(self):
        """A record read back from Neo4j carries ``p_*`` projections and ``kh*`` keys; the read path strips ``kh*``."""
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        doc = E.Record(**_record_kwargs(E.Record, RecordType.FILE, "full")).to_arango_base_record()
        doc["recordProps"] = {"owner": "ann", "tags": ["a", "b"], "score": 1.5}
        stored = provider._arango_to_neo4j_node(doc, CollectionNames.RECORDS.value)
        stored.update({"khSortName": "quarterly report.pdf", "khScope": "x", "p_owner": "ann", "p_tags": ["a", "b"]})
        back = provider._neo4j_to_arango_node(stored, CollectionNames.RECORDS.value)
        assert "khSortName" not in back
        _validate(CollectionNames.RECORDS.value, _stored(back))


class TestEntityPayloads:
    @pytest.mark.parametrize(("cls", "conv", "collection", "req", "mode"), _ENTITY_MODE_CASES)
    def test_converter_output_validates(self, cls, conv, collection, req, mode):
        doc = getattr(_entity(cls, req, mode), conv)()
        _validate(collection, _stored(doc))

    @pytest.mark.parametrize("group_type", list(E.RecordGroupType), ids=lambda g: g.name)
    def test_every_record_group_type(self, group_type):
        kw = _RG_REQ | {"group_type": group_type}
        _validate(CollectionNames.RECORD_GROUPS.value, _stored(E.RecordGroup(**kw).to_arango_base_record_group()))

    @pytest.mark.parametrize("model", [PermissionModel.RECORD_GROUP_LEVEL, PermissionModel.RECORD_LEVEL, None],
                             ids=lambda m: getattr(m, "name", "none"))
    def test_record_group_permission_models(self, model):
        kw = _RG_REQ | {"permission_model": model}
        _validate(CollectionNames.RECORD_GROUPS.value, _stored(E.RecordGroup(**kw).to_arango_base_record_group()))

    @pytest.mark.parametrize("rule", list(AccessRule), ids=lambda r: r.name)
    def test_record_group_access_rules(self, rule):
        kw = _RG_REQ | {"access_rule": rule}
        _validate(CollectionNames.RECORD_GROUPS.value, _stored(E.RecordGroup(**kw).to_arango_base_record_group()))

    @pytest.mark.parametrize("connector", list(Connectors), ids=lambda c: c.name)
    def test_every_connector_on_record_group_role_and_user(self, connector):
        _validate(CollectionNames.RECORD_GROUPS.value,
                  _stored(E.RecordGroup(**(_RG_REQ | {"connector_name": connector})).to_arango_base_record_group()))
        role = E.AppRole(app_name=connector, connector_id="c", source_role_id="R", name="r")
        _validate(CollectionNames.ROLES.value, _stored(role.to_arango_base_role()))
        group = E.AppUserGroup(app_name=connector, connector_id="c", source_user_group_id="G", name="g")
        _validate(CollectionNames.GROUPS.value, _stored(group.to_arango_base_user_group()))

    def test_user_as_neo4j_provider_writes_it(self):
        """``upsert_users`` adds ``id``, ``orgId`` and ``isActive`` to the converter output (neo4j_provider.py)."""
        user = E.AppUser(app_name=Connectors.SLACK, connector_id="c", source_user_id="U", email="a@b.co",
                         full_name="Ann", title="Engineer")
        doc = user.to_arango_base_user()
        doc.update({"id": user.id, "orgId": "org-1", "isActive": False})
        doc.pop("_key")
        _validate(CollectionNames.USERS.value, doc)

    def test_app_documents(self):
        """Apps have no Python converter (the Node service writes them); this mirrors ``AppMetadata.from_db_document``."""
        minimal = {"_key": "c1", "name": "Drive", "type": "GOOGLE_DRIVE", "appGroup": "Google Workspace",
                   "scope": "personal", "isActive": True, "createdAtTimestamp": _NOW}
        full = minimal | {
            "authType": "OAUTH", "isAgentActive": False, "isConfigured": True, "isAuthenticated": True,
            "createdBy": "u", "updatedBy": "u", "authenticatedBy": "u", "lastSyncedBy": None,
            "updatedAtTimestamp": _NOW, "status": "IDLE", "isLocked": False, "permissionModel": "RECORD_LEVEL",
            "vectorMembershipBackfilled": False, "vectorMembershipBackfillAfterKey": None,
            "ownerDeviceId": None, "ownerDeviceName": None,
        }
        for doc in (minimal, full):
            _validate(CollectionNames.APPS.value, _stored(doc))


# ---------------------------------------------------------------------------
# Corpus: raw converter output, `_key` included
# ---------------------------------------------------------------------------
class TestRawConverterOutput:
    @pytest.mark.parametrize(("cls", "rt", "mode", "part"), [p for p in _DOC_MODE_CASES if p.values[2] == "full"])
    def test_converter_output_with_arango_key_is_accepted(self, cls, rt, mode, part):
        """The converters emit ``_key`` and callers validate before conversion.

        The registry adapts ``_key`` to ``id`` (node_schema_registry.adapt_schema), so on every schema that is
        ``additionalProperties: false`` the raw converter output is rejected: "'_key' was unexpected".
        """
        _validate(*_part(cls, rt, mode, part))

    @pytest.mark.parametrize(("cls", "conv", "collection", "req"), _ENTITIES, ids=lambda v: getattr(v, "__name__", None))
    def test_entity_converter_output_with_arango_key_is_accepted(self, cls, conv, collection, req):
        _validate(collection, getattr(_entity(cls, req, "full"), conv)())


# ---------------------------------------------------------------------------
# Neo4j round trip
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def provider() -> Neo4jProvider:
    return Neo4jProvider(logger=MagicMock(), config_service=MagicMock())


def _round_trip(provider, collection: str, doc: dict) -> dict:
    node = provider._arango_to_neo4j_node(doc, collection)
    # Neo4j has no null property: SET n += {k: null} removes it, so a stored node never carries one.
    node = {k: v for k, v in node.items() if v is not None}
    return provider._neo4j_to_arango_node(node, collection)


def _comparable(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if v is not None and k not in ("_key", "_id", "id")}


class TestNeo4jRoundTrip:
    @pytest.mark.parametrize(("cls", "rt", "mode", "part"), _DOC_MODE_CASES)
    def test_record_docs_survive_round_trip_and_validate(self, provider, cls, rt, mode, part):
        collection, doc = _part(cls, rt, mode, part)
        back = _round_trip(provider, collection, doc)
        assert _comparable(back) == _comparable(doc)
        _validate(collection, _stored(back))

    @pytest.mark.parametrize(("cls", "conv", "collection", "req", "mode"), _ENTITY_MODE_CASES)
    def test_entity_docs_survive_round_trip_and_validate(self, provider, cls, conv, collection, req, mode):
        doc = getattr(_entity(cls, req, mode), conv)()
        back = _round_trip(provider, collection, doc)
        assert _comparable(back) == _comparable(doc)
        _validate(collection, _stored(back))

    def test_artifact_nested_values_round_trip_and_validate(self, provider):
        kw = _record_kwargs(E.ArtifactRecord, RecordType.ARTIFACT, "minimal") | {
            "result_schema": {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "object"}}}},
            "versions": [{"registryVersion": 1, "storageVersion": 0, "contentHash": "h", "sizeBytes": 3, "createdAt": 5}],
        }
        doc = E.ArtifactRecord(**kw).to_arango_artifact_record()
        back = _round_trip(provider, CollectionNames.ARTIFACTS.value, doc)
        assert back["resultSchema"] == doc["resultSchema"]
        _validate(CollectionNames.ARTIFACTS.value, _stored(back))

    def test_sql_table_foreign_keys_round_trip_and_validate(self, provider):
        fks = [{"column": "user_id", "ref": {"table": "users", "column": "id"}, "on_delete": None}]
        kw = _record_kwargs(E.SQLTableRecord, RecordType.SQL_TABLE, "minimal") | {"foreign_keys": fks}
        doc = E.SQLTableRecord(**kw).to_arango_record()
        back = _round_trip(provider, CollectionNames.SQL_TABLES.value, doc)
        assert back["foreignKeys"] == fks
        _validate(CollectionNames.SQL_TABLES.value, _stored(back))

    def test_record_props_round_trip_and_validate(self, provider):
        doc = E.Record(**_record_kwargs(E.Record, RecordType.FILE, "full")).to_arango_base_record()
        doc["recordProps"] = {"owner": "ann", "tags": ["a", "b"], "nested": {"k": [1, {"z": None}]}}
        back = _round_trip(provider, CollectionNames.RECORDS.value, doc)
        assert back["recordProps"] == doc["recordProps"]
        _validate(CollectionNames.RECORDS.value, _stored(back))


# One realistic nested value per field the Neo4j path JSON-encodes (JSON_ENCODED_FIELDS).
_NESTED_SAMPLES = {
    ("records", "recordProps"): {"owner": "ann", "tags": ["a", "b"], "score": 1.5, "flag": True},
    ("blocks", "typeTable"): {"a": {"t": "int", "n": [1, 2]}},
    ("blocks", "pendingEdges"): [{"to": "x", "kind": "CALLS", "meta": {"line": 3}}],
    ("agentTemplates", "models"): [{"name": "n", "role": "r", "provider": "p", "config": {"temperature": 0.2, "stop": ["a"]}}],
    ("agentTemplates", "memory"): {"type": ["CONVERSATIONS"], "extra": {"k": 1}},
    ("agentTemplates", "tools"): [{"name": "t", "description": "d"}],
    ("agentSkills", "resources"): {"files": {"a.md": "x"}},
    ("agentSkillVersions", "resources"): {"files": {"a.md": "x"}},
    ("virtualRecordToDocIdMapping", "retired"): [{"key": "k", "at": 1}],
    ("artifacts", "resultSchema"): {"type": "object", "properties": {"a": {"type": "string"}}},
    ("sqlTables", "foreignKeys"): [{"column": "a", "ref": {"table": "t", "column": "id"}}],
}
_NESTED_CASES = [
    pytest.param(c, f, id=f"{c}.{f}") for c, fields in JSON_ENCODED_FIELDS.items() for f in sorted(fields)
]


class TestJsonEncodedFields:
    @pytest.mark.parametrize(("collection", "field"), _NESTED_CASES)
    def test_declared_field_has_a_sample(self, collection, field):
        assert (collection, field) in _NESTED_SAMPLES, "add a realistic nested sample for this encoded field"

    @pytest.mark.parametrize(("collection", "field"), _NESTED_CASES)
    def test_value_survives_write_and_read_and_satisfies_its_property_schema(self, provider, collection, field):
        value = _NESTED_SAMPLES[(collection, field)]
        node = provider._arango_to_neo4j_node({"_key": "k1", field: value}, collection)
        assert isinstance(node[field], str), "Neo4j has no map property: the writer must JSON-encode it"
        back = provider._neo4j_to_arango_node(node, collection)
        assert back[field] == value
        schema = get_node_schema(collection) or (
            adapt_schema(_UNREGISTERED_RULES[collection]) if collection in _UNREGISTERED_RULES else None
        )
        if schema and field in schema["properties"]:
            sub = schema["properties"][field]
            error = best_match(validators.validator_for(sub)(sub).iter_errors(back[field]))
            assert error is None, error.message if error else ""


# ---------------------------------------------------------------------------
# The corpus covers every converter
# ---------------------------------------------------------------------------
# no graph writer: ``User`` is a runtime identity (email lookups), only AppUser reaches the users collection
_EXEMPT = {("User", "to_arango_base_record")}


def test_every_to_arango_converter_in_entities_is_exercised():
    defined = {
        (cls.__name__, name)
        for _, cls in inspect.getmembers(E, inspect.isclass)
        if cls.__module__ == E.__name__
        for name, member in vars(cls).items()
        if name.startswith("to_arango") and callable(member)
    }
    covered = {(cls.__name__, "to_arango_base_record") for cls in _RECORD_CLASSES}
    covered |= {
        (cls.__name__, name)
        for cls in _RECORD_CLASSES
        for name in ("to_arango_record", "to_arango_artifact_record")
    }
    covered |= {(cls.__name__, conv) for cls, conv, *_ in _ENTITIES}
    assert defined - covered - _EXEMPT == set()


def test_every_record_type_is_covered():
    assert {rt for rts in _RECORD_CLASSES.values() for rt in rts} == set(RecordType)
