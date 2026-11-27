"""A storage document id never comes from a request body: only a signed grant from the gateway names one.

The connector service built a KB file record from the client's ``externalRecordId``,
so a caller that reached it directly could point its own record at another user's storage document and read
it back. The gateway is the only party that creates storage documents; it signs the ids it created together
with the org, user, KB and folder. These tests cover the grant, the routes that take it, the service that
uses it, and the push route (whose ``externalRecordId`` is a logical key, never a storage pointer).
"""

import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.sources.localKB.api.kb_router import (
    upload_records_to_folder,
    upload_records_to_kb,
)
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.connectors.sources.localKB.upload_grant import (
    MAX_FUTURE_SKEW_SECONDS,
    MAX_TTL_SECONDS,
    GrantedDocument,
    UploadGrantError,
    derive_grant_key,
    sign_grant,
    verify_grant,
)

SECRET = "scoped-secret-for-tests"
ORG, USER, KB, FOLDER = "org-1", "user-1", "kb-1", "folder-1"
MINE, MINE2, VICTIM = "doc-mine-1", "doc-mine-2", "doc-victim-9"
NOW = 1_800_000_000


def key_of(doc_id: str) -> str:
    return f"key-{doc_id}"


def docs(*doc_ids: str) -> dict[str, str]:
    """The grant's ``{record key: storage document id}``."""
    return {key_of(d): GrantedDocument(d, "application/pdf", "pdf") for d in doc_ids}


def grant(**over) -> str:
    args = {"org_id": ORG, "user_id": USER, "kb_id": KB, "folder_id": None, "documents": docs(MINE, MINE2), "now": NOW}
    args.update(over)
    return sign_grant(SECRET, **args)


def verify(token, **over) -> dict[str, str]:
    args = {"org_id": ORG, "user_id": USER, "kb_id": KB, "folder_id": None, "now": NOW + 5}
    args.update(over)
    return verify_grant(SECRET, token, **args)


def _resign(token: str, mutate) -> str:
    """The same claims, edited, with a valid signature (what a holder of the secret could make)."""
    body = token.split(".")[0]
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    mutate(claims)
    new_body = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
    mac = hmac.new(derive_grant_key(SECRET), new_body.encode(), hashlib.sha256).digest()
    return f"{new_body}.{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"


def _forge(token: str, mutate) -> str:
    body, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    mutate(claims)
    new_body = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
    return f"{new_body}.{sig}"


class TestGrant:
    def test_a_valid_grant_names_exactly_its_documents(self) -> None:
        assert verify(grant()) == docs(MINE, MINE2)

    def test_a_folder_grant_is_for_that_folder(self) -> None:
        assert verify(grant(folder_id=FOLDER), folder_id=FOLDER) == docs(MINE, MINE2)

    @pytest.mark.parametrize(
        "over",
        [{"user_id": "user-2"}, {"org_id": "org-2"}, {"kb_id": "kb-2"}, {"folder_id": "folder-2"}],
        ids=["other-user", "other-org", "other-kb", "other-folder"],
    )
    def test_a_grant_bound_to_something_else_is_refused(self, over) -> None:
        with pytest.raises(UploadGrantError):
            verify(grant(), **over)

    def test_a_root_grant_is_not_a_folder_grant(self) -> None:
        with pytest.raises(UploadGrantError):
            verify(grant(), folder_id=FOLDER)
        with pytest.raises(UploadGrantError):
            verify(grant(folder_id=FOLDER), folder_id=None)

    def test_an_expired_grant_is_refused(self) -> None:
        with pytest.raises(UploadGrantError, match="expired"):
            verify(grant(ttl_seconds=60), now=NOW + 60)

    def test_a_lifetime_beyond_the_maximum_is_refused(self) -> None:
        with pytest.raises(UploadGrantError):
            verify(grant(ttl_seconds=MAX_TTL_SECONDS + 1))

    def test_an_expiry_more_than_the_maximum_ahead_of_now_is_refused(self) -> None:
        with pytest.raises(UploadGrantError):  # minted by a clock far ahead of ours
            verify(grant(now=NOW + 50, ttl_seconds=MAX_TTL_SECONDS), now=NOW - 20)

    @pytest.mark.parametrize("ahead", [1, 30, MAX_FUTURE_SKEW_SECONDS])
    def test_a_full_lifetime_grant_from_a_clock_slightly_ahead_is_accepted(self, ahead) -> None:
        # the gateway always signs the full lifetime; its clock being a little ahead must not refuse every upload
        assert verify(grant(now=NOW + ahead, ttl_seconds=MAX_TTL_SECONDS), now=NOW) == docs(MINE, MINE2)

    def test_a_grant_from_a_clock_ahead_by_more_than_the_skew_is_refused(self) -> None:
        with pytest.raises(UploadGrantError, match="lifetime"):
            verify(grant(now=NOW + MAX_FUTURE_SKEW_SECONDS + 1, ttl_seconds=MAX_TTL_SECONDS), now=NOW)

    def test_the_version_must_be_the_integer_one(self) -> None:
        for bad in (True, "1", 1.0, 2, 0, None):
            with pytest.raises(UploadGrantError):
                verify(_resign(grant(), lambda c, bad=bad: c.update(v=bad)))
        with pytest.raises(UploadGrantError):
            verify(_resign(grant(), lambda c: c.pop("v")))

    @pytest.mark.parametrize("entry", [["k", "d", "application/pdf"], ["k", "d", "application/pdf", None], ["k", "d", "", "pdf"], ["k", "d", "m", "p", "x"]])
    def test_a_document_entry_must_be_key_document_mime_and_extension(self, entry) -> None:
        with pytest.raises(UploadGrantError):
            verify(_resign(grant(), lambda c: c.update(documents=[entry])))

    def test_an_unknown_claim_is_refused(self) -> None:
        with pytest.raises(UploadGrantError):
            verify(_resign(grant(), lambda c: c.update(extra="x")))

    def test_the_signature_has_one_form(self) -> None:
        token = grant()
        body, sig = token.split(".")
        for bad in (sig + "=", sig + "A", sig[:-1], sig + "\n", sig[:-1] + "!", ""):
            with pytest.raises(UploadGrantError):
                verify(f"{body}.{bad}")

    def test_the_body_has_one_form(self) -> None:
        body, sig = grant().split(".")
        with pytest.raises(UploadGrantError):  # padded body, same bytes: a second string for one grant
            verify(f"{body}=.{sig}")

    def test_a_grant_from_the_future_is_refused(self) -> None:
        with pytest.raises(UploadGrantError):
            verify(grant(now=NOW + 3600), now=NOW)

    def test_a_tampered_document_id_is_refused(self) -> None:
        forged = _forge(grant(), lambda c: c["documents"].append([key_of(VICTIM), VICTIM, "application/pdf", "pdf"]))
        with pytest.raises(UploadGrantError, match="bad signature"):
            verify(forged)

    def test_a_tampered_binding_is_refused(self) -> None:
        forged = _forge(grant(user_id="user-2"), lambda c: c.update(userId=USER))
        with pytest.raises(UploadGrantError, match="bad signature"):
            verify(forged)

    def test_a_grant_signed_with_another_secret_is_refused(self) -> None:
        other = sign_grant("another-secret", org_id=ORG, user_id=USER, kb_id=KB, folder_id=None, documents=docs(MINE), now=NOW)
        with pytest.raises(UploadGrantError):
            verify(other)

    @pytest.mark.parametrize("token", [None, "", 5, {}, ["a.b"], "nodot", "a.b.c", "!!.!!", "e30.AAAA"])
    def test_a_missing_or_malformed_grant_is_refused(self, token) -> None:
        with pytest.raises(UploadGrantError):
            verify(token)

    def test_a_service_token_is_not_a_grant(self) -> None:
        import jwt

        token = jwt.encode({"orgId": ORG, "userId": USER, "kbId": KB}, SECRET, algorithm="HS256")
        with pytest.raises(UploadGrantError):
            verify(token)

    def test_the_key_is_not_the_raw_secret(self) -> None:
        assert derive_grant_key(SECRET) != SECRET.encode()
        with pytest.raises(UploadGrantError):
            derive_grant_key("")

    def test_the_node_signer_and_this_one_agree(self) -> None:
        """Fixed vector shared with tests/libs/utils/kbUploadGrant.test.ts."""
        token = sign_grant("vector-secret", org_id="o", user_id="u", kb_id="k", folder_id="f", documents={"kb": GrantedDocument("b", "text/b", "pdf"), "ka": GrantedDocument("a", "text/a", "")}, now=1000)
        assert token == (
            "eyJkb2N1bWVudHMiOltbImthIiwiYSIsInRleHQvYSIsIiJdLFsia2IiLCJiIiwidGV4dC9iIiwicGRmIl1dLCJleHAiOjEzMDAsImZvbGRlck"
            "lkIjoiZiIsImlhdCI6MTAwMCwia2JJZCI6ImsiLCJvcmdJZCI6Im8iLCJ1c2VySWQiOiJ1IiwidiI6MX0.beAZ_yJDODilzVKHMv0rKTlqhpAOUBxA3JyP7WIEOIw"
        )


def _request(body, user=None) -> MagicMock:
    request = MagicMock()
    data = user or {"userId": USER, "orgId": ORG}
    request.state.user = MagicMock()
    request.state.user.get = MagicMock(side_effect=lambda k: data.get(k))
    request.json = AsyncMock(return_value=body)
    request.app.container.logger.return_value = MagicMock()
    return request


def _kb_service() -> AsyncMock:
    svc = AsyncMock()
    svc.config_service = AsyncMock()
    svc.config_service.get_config = AsyncMock(return_value={"scopedJwtSecret": SECRET})
    svc.upload_records_to_kb = AsyncMock(return_value={"success": True})
    svc.upload_records_to_folder = AsyncMock(return_value={"success": True})
    return svc


def _files(*doc_ids) -> list:
    """What the gateway sends: a record and a file record per file, each pointing at its storage document. The name has
    its extension stripped and the extension is sent separately (utils.ts getFilenameWithoutExtension)."""
    return [
        {
            "record": {
                "_key": key_of(d), "orgId": ORG, "recordName": d, "externalRecordId": d, "recordType": "FILE",
                "origin": "UPLOAD", "connectorId": KB, "createdAtTimestamp": 1_000, "updatedAtTimestamp": 1_000,
                "sourceCreatedAtTimestamp": 1_000, "sourceLastModifiedTimestamp": 1_000, "version": 1,
            },
            "fileRecord": {"_key": key_of(d), "orgId": ORG, "name": d, "isFile": True, "extension": "pdf", "mimeType": "application/pdf"},
            "filePath": f"{d}.txt",
            "lastModified": 1_000,
        }
        for i, d in enumerate(doc_ids)
    ]


async def _root(body, svc, **req) -> object:
    return await upload_records_to_kb(kb_id=KB, request=_request(body, **req), kb_service=svc, kafka_service=AsyncMock())


async def _folder(body, svc, **req) -> object:
    return await upload_records_to_folder(
        kb_id=KB, folder_id=FOLDER, request=_request(body, **req), kb_service=svc, kafka_service=AsyncMock()
    )


@pytest.fixture(autouse=True)
def _clock(monkeypatch) -> None:
    monkeypatch.setattr("app.connectors.sources.localKB.upload_grant.time.time", lambda: NOW + 5)


class TestRoutes:
    @pytest.mark.asyncio
    async def test_a_legitimate_root_upload_passes_the_granted_ids_through(self) -> None:
        svc = _kb_service()
        result = await _root({"files": _files(MINE, MINE2), "grant": grant()}, svc)
        assert result["success"] is True
        assert svc.upload_records_to_kb.await_args.kwargs["granted_documents"] == docs(MINE, MINE2)

    @pytest.mark.asyncio
    async def test_a_legitimate_folder_upload_passes(self) -> None:
        svc = _kb_service()
        await _folder({"files": _files(MINE), "grant": grant(folder_id=FOLDER, documents=docs(MINE))}, svc)
        assert svc.upload_records_to_folder.await_args.kwargs["granted_documents"] == docs(MINE)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("call", [_root, _folder], ids=["root", "folder"])
    async def test_no_grant_is_refused_and_nothing_is_written(self, call) -> None:
        svc = _kb_service()
        with pytest.raises(HTTPException) as e:
            await call({"files": _files(VICTIM)}, svc)
        assert e.value.status_code == 403
        svc.upload_records_to_kb.assert_not_awaited()
        svc.upload_records_to_folder.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("call", [_root, _folder], ids=["root", "folder"])
    @pytest.mark.parametrize(
        "bad",
        [
            lambda f: grant(user_id="user-2", folder_id=f),
            lambda f: grant(org_id="org-2", folder_id=f),
            lambda f: grant(kb_id="kb-2", folder_id=f),
            lambda f: grant(folder_id="other-folder"),
            lambda f: grant(folder_id=f, ttl_seconds=1),
            lambda f: _forge(grant(folder_id=f), lambda c: c["documents"].append([key_of(VICTIM), VICTIM, "application/pdf", "pdf"])),
        ],
        ids=["user", "org", "kb", "folder", "expired", "tampered"],
    )
    async def test_a_grant_for_something_else_is_refused_and_nothing_is_written(self, call, bad) -> None:
        svc = _kb_service()
        token = bad(FOLDER if call is _folder else None)
        with pytest.raises(HTTPException) as e:
            await call({"files": _files(VICTIM), "grant": token}, svc)
        assert e.value.status_code == 403
        svc.upload_records_to_kb.assert_not_awaited()
        svc.upload_records_to_folder.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_grant_that_cannot_be_checked_is_a_refusal(self) -> None:
        svc = _kb_service()
        svc.config_service.get_config = AsyncMock(return_value={})
        with pytest.raises(HTTPException) as e:
            await _root({"files": _files(MINE), "grant": grant()}, svc)
        assert e.value.status_code == 403
        svc.upload_records_to_kb.assert_not_awaited()
        svc.config_service.get_config = AsyncMock(side_effect=RuntimeError("config down"))
        with pytest.raises(HTTPException) as e:
            await _root({"files": _files(MINE), "grant": grant()}, svc)
        assert e.value.status_code == 403


def _real_service() -> tuple:
    gp = MagicMock()
    gp._validate_upload_context = AsyncMock(return_value={"valid": True, "user": {"email": "a@b.c", "fullName": "A"}})
    gp._analyze_upload_structure = MagicMock(
        side_effect=lambda files, validation: {
            "sorted_folder_paths": [], "parent_folder_id": None, "file_destinations": {i: {"type": "root"} for i in range(len(files))},
        }
    )
    gp._populate_file_destinations = MagicMock()
    gp._fetch_existing_file_names_in_parent = AsyncMock(return_value=set())
    gp._normalize_name = MagicMock(side_effect=lambda n: n)
    gp._normalized_name_variants_lower = MagicMock(side_effect=lambda n: [n.lower()])
    gp._generate_upload_message = MagicMock(return_value="ok")
    processor = MagicMock()
    processor.on_new_records = AsyncMock()
    svc = KnowledgeBaseService(
        MagicMock(), gp, AsyncMock(), processor_for_kb=AsyncMock(return_value=processor), config_service=AsyncMock()
    )
    svc._refuse_if_deleting = AsyncMock(return_value=None)
    svc._exceeds_folder_depth = AsyncMock(return_value=False)
    svc._get_storage_url = AsyncMock(return_value="http://storage.test")
    return svc, processor


class TestService:
    @pytest.mark.asyncio
    async def test_a_foreign_external_record_id_is_refused_and_nothing_is_written(self) -> None:
        svc, processor = _real_service()
        result = await svc.upload_records_to_kb(KB, USER, ORG, _files(VICTIM), docs(MINE))
        assert result["success"] is False and result["code"] == 403
        processor.on_new_records.assert_not_awaited()
        svc.graph_provider._validate_upload_context.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_foreign_id_in_a_batch_refuses_the_whole_batch(self) -> None:
        svc, processor = _real_service()
        result = await svc.upload_records_to_kb(KB, USER, ORG, _files(MINE, VICTIM), docs(MINE))
        assert result["success"] is False and result["code"] == 403
        processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("missing", [None, "", 7, ["x"]])
    async def test_a_file_without_a_usable_id_is_refused(self, missing) -> None:
        svc, processor = _real_service()
        files = _files(MINE)
        files[0]["record"]["_key"] = missing
        result = await svc.upload_records_to_kb(KB, USER, ORG, files, docs(MINE))
        assert result["code"] == 403
        processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_granted_id_cannot_back_two_records(self) -> None:
        svc, processor = _real_service()
        result = await svc.upload_records_to_kb(KB, USER, ORG, _files(MINE, MINE), docs(MINE))
        assert result["code"] == 403
        processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_empty_grant_admits_nothing(self) -> None:
        svc, processor = _real_service()
        result = await svc.upload_records_to_kb(KB, USER, ORG, _files(MINE), {})
        assert result["code"] == 403
        processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_multi_file_upload_with_granted_ids_creates_each_record_from_its_own_document(self) -> None:
        svc, processor = _real_service()
        result = await svc.upload_records_to_kb(KB, USER, ORG, _files(MINE, MINE2), docs(MINE, MINE2))
        assert result["success"] is True and result["totalCreated"] == 2
        entities = processor.on_new_records.await_args.args[0]
        built = {fr.external_record_id: fr for fr, _ in entities}
        assert set(built) == {MINE, MINE2}
        for doc, fr in built.items():
            assert fr.fetch_signed_url == f"http://storage.test/api/v1/document/internal/{doc}/download"
            assert fr.uploaded_by == USER

    @pytest.mark.asyncio
    async def test_a_folder_upload_with_granted_ids_creates_the_records(self) -> None:
        svc, processor = _real_service()
        svc.graph_provider._analyze_upload_structure = MagicMock(
            return_value={"sorted_folder_paths": [], "parent_folder_id": FOLDER, "file_destinations": {0: {"type": "root"}}}
        )
        result = await svc.upload_records_to_folder(KB, FOLDER, USER, ORG, _files(MINE), docs(MINE))
        assert result["success"] is True
        entities = processor.on_new_records.await_args.args[0]
        assert [fr.external_record_id for fr, _ in entities] == [MINE]

    @pytest.mark.asyncio
    async def test_a_foreign_folder_upload_is_refused(self) -> None:
        svc, processor = _real_service()
        result = await svc.upload_records_to_folder(KB, FOLDER, USER, ORG, _files(VICTIM), docs(MINE))
        assert result["code"] == 403
        processor.on_new_records.assert_not_awaited()

    def test_the_record_builder_refuses_an_id_the_grant_does_not_name(self) -> None:
        svc, _ = _real_service()
        record = {"_key": "r", "orgId": ORG, "recordName": "a.txt", "externalRecordId": VICTIM, "recordType": "FILE", "origin": "UPLOAD", "connectorId": KB, "version": 1}
        with pytest.raises(ValueError):
            svc._build_kb_file_record(KB, record, {"_key": "r", "orgId": ORG, "name": "a.txt"}, None, "http://s", org_id=ORG, granted_documents=docs(MINE))
        with pytest.raises(ValueError):  # no grant at all: default deny
            svc._build_kb_file_record(KB, record, {"_key": "r", "orgId": ORG, "name": "a.txt"}, None, "http://s", org_id=ORG)


FORBIDDEN_FROM_BODY = {
    "id": "forged-id", "orgId": "other-org", "connectorId": "other-kb", "externalRecordId": VICTIM,
    "storageDocumentId": VICTIM, "signedUrlRoute": f"http://x/{VICTIM}", "signedUrl": "http://x/y", "fetchSignedUrl": "http://x/z",
    "webUrl": "javascript:alert(1)", "recordType": "WEBPAGE", "origin": "CONNECTOR", "connectorName": "GOOGLE_DRIVE",
    "pushOrigin": True, "externalRevisionId": "rev-forged", "isDeleted": True, "isArchived": True, "deletedByUserId": "x",
    "recordGroupId": "rg-x", "rootRecordGroupId": "rg-y", "parentNodeId": "p-x", "externalGroupId": "g-x", "externalParentId": "ep-x",
    "externalRecordGroupId": "g-x", "accessRule": "OPEN", "uploadedBy": "attacker", "version": 99, "mimeType": "text/html",
    "indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED", "virtualRecordId": "vr-x", "isPlaceholder": True,
    "isInternal": True, "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "path": f"storage://{VICTIM}",
    "localFsRelativePath": "../../etc/passwd",
}


class TestBodyFieldsNeverPointAtStorage:
    @pytest.mark.asyncio
    async def test_every_forbidden_field_in_the_body_is_ignored_and_the_record_holds_the_servers_values(self) -> None:
        svc, processor = _real_service()
        files = _files(MINE)
        files[0]["record"].update(FORBIDDEN_FROM_BODY)
        files[0]["fileRecord"].update(FORBIDDEN_FROM_BODY)
        files[0]["fileRecord"]["_key"] = "forged-file-key"
        with patch("app.connectors.sources.localKB.handlers.kb_service.get_epoch_timestamp_in_ms", return_value=5_000):
            result = await svc.upload_records_to_kb(KB, USER, ORG, files, docs(MINE))
        assert result["success"] is True
        (fr, _), = processor.on_new_records.await_args.args[0]
        stored = fr.to_arango_base_record()
        assert fr.id == key_of(MINE) and fr.org_id == ORG and fr.connector_id == KB
        assert fr.external_record_id == MINE
        assert fr.fetch_signed_url == f"http://storage.test/api/v1/document/internal/{MINE}/download"
        assert fr.storage_document_id is None and fr.path is None and fr.local_fs_relative_path is None
        assert fr.weburl == f"/record/{key_of(MINE)}"
        assert fr.record_type.value == "FILE" and fr.origin.value == "UPLOAD" and fr.connector_name.value == "KB"
        assert fr.push_origin is False and fr.external_revision_id is None and fr.version == 1
        assert fr.mime_type == "application/pdf"  # what the gateway detected, not the body's "text/html"
        assert fr.is_deleted is False and fr.deleted_by_user_id is None and fr.uploaded_by == USER
        assert fr.record_group_id is None and fr.root_record_group_id is None and fr.parent_node_id is None
        assert fr.access_rule.value == "OPEN" and fr.virtual_record_id is None
        assert fr.indexing_status == "QUEUED" and fr.is_placeholder is False and fr.is_internal is False
        assert fr.created_at == 5_000 and fr.updated_at == 5_000
        assert fr.record_name == MINE
        assert "javascript" not in json.dumps(stored, default=str)

    @pytest.mark.asyncio
    async def test_a_javascript_web_url_is_never_stored(self) -> None:
        svc, processor = _real_service()
        files = _files(MINE)
        files[0]["record"]["webUrl"] = "javascript:alert(1)"
        files[0]["fileRecord"]["webUrl"] = "javascript:alert(1)"
        await svc.upload_records_to_kb(KB, USER, ORG, files, docs(MINE))
        (fr, _), = processor.on_new_records.await_args.args[0]
        assert fr.weburl == f"/record/{key_of(MINE)}"

    @pytest.mark.asyncio
    async def test_a_file_record_path_naming_storage_is_never_stored(self) -> None:
        svc, processor = _real_service()
        files = _files(MINE)
        files[0]["fileRecord"]["path"] = f"storage://{VICTIM}"
        files[0]["record"]["path"] = f"storage://{VICTIM}"
        await svc.upload_records_to_kb(KB, USER, ORG, files, docs(MINE))
        (fr, _), = processor.on_new_records.await_args.args[0]
        assert fr.path is None
        assert VICTIM not in json.dumps(fr.to_arango_base_record(), default=str)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "name, stored",
        [
            ("Meeting 5.30pm notes", "Meeting 5.30pm notes"),
            ("v1.0", "v1.0"),  # the gateway strips ".pdf": the name still has a dot
            ("v1.0-final (1)", "v1.0-final (1)"),
            ("Report", "Report"),
            ("tab\there\x00name", "tabherename"),
            ("  padded name  ", "padded name"),
            ("a" * 300, "a" * 255),
            ("é" * 300, "é" * 255),
        ],
    )
    async def test_ordinary_names_are_kept_and_the_extension_comes_from_the_grant_not_the_name(self, name, stored) -> None:
        svc, processor = _real_service()
        files = _files(MINE)
        files[0]["fileRecord"]["name"] = name
        files[0]["fileRecord"]["extension"] = "../../forged"  # the body's extension is never used
        result = await svc.upload_records_to_kb(KB, USER, ORG, files, docs(MINE))
        assert result["success"] is True and result["totalCreated"] == 1
        (fr, _), = processor.on_new_records.await_args.args[0]
        assert fr.record_name == stored
        assert fr.extension == "pdf"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "granted, expected",
        [("pdf", "pdf"), (".PDF", "pdf"), ("tar.gz", None), ("", None), ("a/b", None), ("x" * 40, None), ("2024-05 draft", None), ("résumé", None), ("..", None)],
    )
    async def test_the_extension_is_the_grants_cleaned_or_none(self, granted, expected) -> None:
        svc, processor = _real_service()
        grant_docs = {key_of(MINE): GrantedDocument(MINE, "application/pdf", granted)}
        result = await svc.upload_records_to_kb(KB, USER, ORG, _files(MINE), grant_docs)
        assert result["success"] is True
        (fr, _), = processor.on_new_records.await_args.args[0]
        assert fr.extension == expected

    @pytest.mark.asyncio
    async def test_indexing_receives_pdf_for_an_uploaded_pdf(self) -> None:
        """End to end through the real service and the processor's event payload: what the indexing consumer reads
        (event ``extension`` and ``mimeType``) for a file the gateway named "v1.0" with extension "pdf"."""
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )

        svc, processor = _real_service()
        files = _files(MINE)
        files[0]["fileRecord"]["name"] = "v1.0"
        files[0]["record"]["recordName"] = "v1.0"
        await svc.upload_records_to_kb(KB, USER, ORG, files, docs(MINE))
        (fr, _), = processor.on_new_records.await_args.args[0]
        payload = DataSourceEntitiesProcessor._record_event_payload(MagicMock(), fr)
        assert payload["extension"] == "pdf" and payload["mimeType"] == "application/pdf"
        assert payload["recordName"] == "v1.0"
        assert fr.to_arango_record()["extension"] == "pdf"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "value, expected",
        [(1_700_000_000_123.9, 1_700_000_000_123), ("now", 5_000), (-5, 5_000), (float("nan"), 5_000), (True, 5_000), (None, 5_000), (1 << 60, 5_000)],
    )
    async def test_a_bad_timestamp_becomes_the_upload_time_and_a_fraction_is_truncated(self, value, expected) -> None:
        svc, processor = _real_service()
        files = _files(MINE)
        files[0]["record"]["sourceCreatedAtTimestamp"] = value
        files[0]["record"]["sourceLastModifiedTimestamp"] = value
        with patch("app.connectors.sources.localKB.handlers.kb_service.get_epoch_timestamp_in_ms", return_value=5_000):
            result = await svc.upload_records_to_kb(KB, USER, ORG, files, docs(MINE))
        assert result["success"] is True
        (fr, _), = processor.on_new_records.await_args.args[0]
        assert fr.source_created_at == expected and fr.source_updated_at == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("size", [-1, "9", True, 1 << 60, 1.5])
    async def test_a_bad_size_is_stored_as_unknown_not_refused(self, size) -> None:
        svc, processor = _real_service()
        files = _files(MINE)
        files[0]["record"]["sizeInBytes"] = size
        result = await svc.upload_records_to_kb(KB, USER, ORG, files, docs(MINE))
        assert result["success"] is True
        (fr, _), = processor.on_new_records.await_args.args[0]
        assert fr.size_in_bytes is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["", "   ", "\x00\x01\t", 5, None])
    async def test_a_file_with_no_usable_name_fails_alone_and_the_rest_are_stored(self, name) -> None:
        svc, processor = _real_service()
        files = _files(MINE, MINE2)
        files[1]["fileRecord"]["name"] = name
        files[1]["record"].pop("recordName")
        result = await svc.upload_records_to_kb(KB, USER, ORG, files, docs(MINE, MINE2))
        assert result["success"] is True and result["totalCreated"] == 1
        assert result["failedFiles"] == [files[1]["filePath"]]
        entities = processor.on_new_records.await_args.args[0]
        assert [fr.external_record_id for fr, _ in entities] == [MINE]

    @pytest.mark.asyncio
    async def test_an_upload_where_no_file_has_a_usable_name_is_a_400_and_writes_nothing(self) -> None:
        svc, processor = _real_service()
        files = _files(MINE)
        files[0]["fileRecord"]["name"] = "  "
        files[0]["record"].pop("recordName")
        result = await svc.upload_records_to_kb(KB, USER, ORG, files, docs(MINE))
        assert result["success"] is False and result["code"] == 400
        processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_duplicate_name_check_uses_the_gateways_mime_type(self) -> None:
        svc, processor = _real_service()
        files = _files(MINE)
        files[0]["record"]["mimeType"] = "text/html"
        await svc.upload_records_to_kb(KB, USER, ORG, files, docs(MINE))
        call = svc.graph_provider._fetch_existing_file_names_in_parent
        assert call.await_count == 1  # the check ran; the record carries the grant's type
        (fr, _), = processor.on_new_records.await_args.args[0]
        assert fr.mime_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_a_record_key_the_grant_does_not_name_is_refused_even_when_the_document_is_granted(self) -> None:
        svc, processor = _real_service()
        files = _files(MINE)
        files[0]["record"]["_key"] = "someone-elses-record-key"
        result = await svc.upload_records_to_kb(KB, USER, ORG, files, docs(MINE))
        assert result["code"] == 403
        processor.on_new_records.assert_not_awaited()


class TestFilesShape:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("call", [_root, _folder], ids=["root", "folder"])
    @pytest.mark.parametrize(
        "files",
        ["abc", 5, [None], [5], {"a": 1}, [{"record": "x", "fileRecord": {}}], [{"record": {}, "fileRecord": None}], [{}], None],
        ids=repr,
    )
    async def test_a_malformed_files_body_is_a_400_before_any_other_work(self, call, files) -> None:
        svc = _kb_service()
        with pytest.raises(HTTPException) as e:
            await call({"files": files, "grant": grant()}, svc)
        assert e.value.status_code == 400
        svc.config_service.get_config.assert_not_awaited()
        svc.upload_records_to_kb.assert_not_awaited()
        svc.upload_records_to_folder.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [[], "x", 5, None])
    async def test_a_body_that_is_not_an_object_is_a_400(self, body) -> None:
        with pytest.raises(HTTPException) as e:
            await _root(body, _kb_service())
        assert e.value.status_code == 400
