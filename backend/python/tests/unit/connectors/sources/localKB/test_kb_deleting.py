"""A knowledge base being deleted: the DELETING marker, the method classification, the write refusals (T42d)."""

from __future__ import annotations

import inspect
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.localKB.api import kb_router
from app.connectors.sources.localKB.api.models import KnowledgeBaseResponse
from app.connectors.sources.localKB.handlers import kb_service as kb_module
from app.connectors.sources.localKB.handlers.kb_service import (
    KB_METHOD_CLASS,
    KnowledgeBaseService,
)
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING

ARGS = {
    "update_knowledge_base": {"kb_id": "kb1", "user_id": "u1", "updates": {"name": "n"}},
    "create_folder_in_kb": {"kb_id": "kb1", "name": "f", "user_id": "u1", "org_id": "org1"},
    "create_nested_folder": {"kb_id": "kb1", "parent_folder_id": "p", "name": "f", "user_id": "u1", "org_id": "org1"},
    "updateFolder": {"folder_id": "f1", "kb_id": "kb1", "user_id": "u1", "name": "n"},
    "delete_folder": {"kb_id": "kb1", "folder_id": "f1", "user_id": "u1"},
    "update_record": {"user_id": "u1", "record_id": "r1", "updates": {"recordName": "x"}},
    "delete_records_in_kb": {"kb_id": "kb1", "record_ids": ["r1"], "user_id": "u1"},
    "delete_records_in_folder": {"kb_id": "kb1", "folder_id": "f1", "record_ids": ["r1"], "user_id": "u1"},
    "restore_record": {"record_id": "r1", "user_id": "u1", "org_id": "org1"},
    "restore_records": {"record_ids": ["r1"], "user_id": "u1", "org_id": "org1"},
    "create_kb_permissions": {"kb_id": "kb1", "requester_id": "u1", "user_ids": ["u2"], "team_ids": [], "role": "READER"},
    "update_kb_permission": {"kb_id": "kb1", "requester_id": "u1", "user_ids": ["u2"], "team_ids": [], "new_role": "READER"},
    "remove_kb_permission": {"kb_id": "kb1", "requester_id": "u1", "user_ids": ["u2"], "team_ids": []},
    "upload_records_to_kb": {"kb_id": "kb1", "user_id": "u1", "org_id": "org1", "files": []},
    "upload_records_to_folder": {"kb_id": "kb1", "folder_id": "f1", "user_id": "u1", "org_id": "org1", "files": []},
    "validate_folder_for_upload": {"kb_id": "kb1", "folder_id": "f1", "user_id": "u1", "org_id": "org1"},
    "move_record": {"kb_id": "kb1", "record_id": "r1", "new_parent_id": None, "user_id": "u1"},
}


def public_coroutines(cls: type = KnowledgeBaseService) -> set[str]:
    return {n for n, f in inspect.getmembers(cls, inspect.iscoroutinefunction) if not n.startswith("_")}


def unclassified(table: dict[str, str], cls: type = KnowledgeBaseService) -> set[str]:
    return public_coroutines(cls) - set(table)


def write_methods() -> set[str]:
    return {n for n, c in KB_METHOD_CLASS.items() if c == "write"}


def arrange(service: KnowledgeBaseService, *, status: str | None) -> None:
    kb = {"_key": "kb1", "id": "kb1", "orgId": "org1", "origin": "UPLOAD", "connectorId": "kb1",
          "recordName": "r", "status": status}
    g = service.graph_provider
    g.get_user_by_user_id = AsyncMock(return_value={"id": "uk1", "orgId": "org1"})
    g.get_user_kb_permission = AsyncMock(return_value="OWNER")
    g.get_document = AsyncMock(return_value=kb)
    g._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb1"})
    g._validate_folder_creation = AsyncMock(return_value={"valid": True})
    g._validate_upload_context = AsyncMock(return_value={"valid": True})
    g.validate_folder_for_upload = AsyncMock(return_value={"valid": True})
    g.validate_folder_in_kb = AsyncMock(return_value=True)


def test_every_public_coroutine_is_classified() -> None:
    assert unclassified(KB_METHOD_CLASS) == set()
    assert set(KB_METHOD_CLASS) <= public_coroutines()
    assert write_methods() == set(ARGS), "a write method without a refusal case here"


def test_the_classification_check_catches_a_missing_entry() -> None:
    table = {k: v for k, v in KB_METHOD_CLASS.items() if k != "updateFolder"}
    assert unclassified(table) == {"updateFolder"}


def test_a_new_public_coroutine_fails_until_classified() -> None:
    class Extended(KnowledgeBaseService):
        async def archive_everything(self) -> None: ...

    assert unclassified(KB_METHOD_CLASS, Extended) == {"archive_everything"}


@pytest.mark.parametrize("method", sorted(ARGS))
@pytest.mark.asyncio
async def test_each_write_method_refuses_a_deleting_kb(service, method) -> None:
    arrange(service, status=APP_STATUS_DELETING)
    with patch.object(kb_module, "is_soft_delete_enabled", AsyncMock(return_value=True)):
        result = await getattr(service, method)(**ARGS[method])
    assert (result["success"], result["code"], result.get("errorCode")) == (False, 409, "KB_BEING_DELETED"), result


@pytest.mark.parametrize("method", sorted(ARGS))
@pytest.mark.asyncio
async def test_each_write_method_is_not_refused_by_a_live_kb(service, method) -> None:
    """Negative control: the refusal comes from the marker, not from the arrangement."""
    arrange(service, status=None)
    with patch.object(kb_module, "is_soft_delete_enabled", AsyncMock(return_value=True)):
        result = await getattr(service, method)(**ARGS[method])
    assert (result or {}).get("errorCode") != "KB_BEING_DELETED"


@pytest.mark.asyncio
async def test_a_user_without_a_role_cannot_learn_that_the_kb_is_being_deleted(service) -> None:
    arrange(service, status=APP_STATUS_DELETING)
    service.graph_provider.get_user_kb_permission = AsyncMock(return_value=None)
    result = await service.update_knowledge_base(**ARGS["update_knowledge_base"])
    assert result["code"] == 404
    service.graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
    assert (await service.update_knowledge_base(**ARGS["update_knowledge_base"]))["code"] == 403


@pytest.mark.asyncio
async def test_an_unreadable_kb_state_is_not_treated_as_live(service) -> None:
    arrange(service, status=None)
    service.graph_provider.get_document = AsyncMock(side_effect=RuntimeError("store down"))
    result = await service.update_knowledge_base(**ARGS["update_knowledge_base"])
    assert result["success"] is False and result["code"] == 500


@pytest.mark.asyncio
async def test_reads_and_the_delete_itself_are_allowed_on_a_deleting_kb(service) -> None:
    arrange(service, status=APP_STATUS_DELETING)
    service.graph_provider.get_knowledge_base = AsyncMock(return_value={"id": "kb1", "status": "DELETING"})
    got = await service.get_knowledge_base("kb1", "u1")
    assert got["status"] == "DELETING"
    service.graph_provider.delete_connector_instance = AsyncMock(return_value={"success": True, "virtual_record_ids": []})
    deleted = await service.delete_knowledge_base("kb1", "u1", "org1")
    assert deleted["success"] is True


def test_the_kb_response_carries_the_status() -> None:
    body = {"id": "kb1", "name": "n", "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "createdBy": "u"}
    assert KnowledgeBaseResponse(**body, status="DELETING").model_dump()["status"] == "DELETING"
    assert KnowledgeBaseResponse(**body).model_dump()["status"] is None


def test_every_mutating_router_handler_maps_to_a_classified_method() -> None:
    seen = {}
    for route in kb_router.kb_router.routes:
        if not set(getattr(route, "methods", ())) & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        called = set(re.findall(r"kb_service\.(\w+)\(", inspect.getsource(route.endpoint)))
        seen[(tuple(sorted(route.methods)), route.path)] = called
        assert called, f"{route.path} calls no service method"
        assert called <= set(KB_METHOD_CLASS), f"{route.path}: {called - set(KB_METHOD_CLASS)} unclassified"
    assert len(seen) >= 15


# ---- the marker -------------------------------------------------------------------------------

def owner(service: KnowledgeBaseService, *, status: str | None = None) -> list[str]:
    arrange(service, status=status)
    order: list[str] = []
    g = service.graph_provider

    async def update(**kw: object) -> bool:
        order.append(f"status={kw['updates']['status']}")
        return True

    async def cascade(**kw: object) -> dict:
        order.append("cascade")
        return {"success": True, "virtual_record_ids": []}

    g.update_knowledge_base = AsyncMock(side_effect=update)
    g.delete_connector_instance = AsyncMock(side_effect=cascade)
    return order


@pytest.mark.asyncio
async def test_the_marker_is_committed_before_the_cascade(service) -> None:
    order = owner(service)
    assert (await service.delete_knowledge_base("kb1", "u1", "org1"))["success"] is True
    assert order == [f"status={APP_STATUS_DELETING}", "cascade"]


@pytest.mark.asyncio
async def test_a_failure_before_the_cascade_puts_the_status_back(service) -> None:
    order = owner(service)
    service.graph_provider.get_uploaded_document_ids = AsyncMock(side_effect=RuntimeError("list failed"))
    result = await service.delete_knowledge_base("kb1", "u1", "org1")
    assert result["code"] == 503
    assert order == [f"status={APP_STATUS_DELETING}", "status=None"]


@pytest.mark.asyncio
async def test_an_exception_before_the_cascade_puts_the_status_back(service) -> None:
    order = owner(service)
    with patch.object(service, "_schedule_upload_removal", AsyncMock(side_effect=RuntimeError("boom"))):
        result = await service.delete_knowledge_base("kb1", "u1", "org1")
    assert result["code"] == 500
    assert order == [f"status={APP_STATUS_DELETING}", "status=None"]


@pytest.mark.asyncio
async def test_a_failed_cascade_leaves_the_kb_deleting(service) -> None:
    order = owner(service)
    service.graph_provider.delete_connector_instance = AsyncMock(return_value={"success": False, "error": "x"})
    result = await service.delete_knowledge_base("kb1", "u1", "org1")
    assert result["code"] == 500
    assert order == [f"status={APP_STATUS_DELETING}"]


@pytest.mark.asyncio
async def test_a_retry_that_fails_early_does_not_restore_a_deleting_status(service) -> None:
    """The first attempt may have passed the cascade's point of no return."""
    order = owner(service, status=APP_STATUS_DELETING)
    service.graph_provider.get_uploaded_document_ids = AsyncMock(side_effect=RuntimeError("list failed"))
    assert (await service.delete_knowledge_base("kb1", "u1", "org1"))["code"] == 503
    assert order == []


@pytest.mark.asyncio
async def test_the_previous_status_is_what_comes_back(service) -> None:
    order = owner(service, status="PAUSED")
    service.graph_provider.get_uploaded_document_ids = AsyncMock(side_effect=RuntimeError("list failed"))
    await service.delete_knowledge_base("kb1", "u1", "org1")
    assert order == [f"status={APP_STATUS_DELETING}", "status=PAUSED"]


@pytest.mark.asyncio
async def test_finish_delete_runs_the_cascade_only_for_a_deleting_kb(service) -> None:
    order = owner(service, status=APP_STATUS_DELETING)
    assert (await service.finish_delete("org1", "kb1"))["success"] is True
    assert order == ["cascade"]
    live = owner(service, status=None)
    refused = await service.finish_delete("org1", "kb1")
    assert refused["code"] == 409 and live == []
    service.graph_provider.get_document = AsyncMock(return_value=None)
    assert (await service.finish_delete("org1", "kb1"))["success"] is True


@pytest.mark.asyncio
async def test_finish_delete_never_raises(service) -> None:
    arrange(service, status=APP_STATUS_DELETING)
    service.graph_provider.delete_connector_instance = AsyncMock(side_effect=RuntimeError("boom"))
    assert (await service.finish_delete("org1", "kb1"))["success"] is False




def test_the_refusal_wording_is_the_one_the_gateway_recognises() -> None:
    """backend-error.ts KB_BEING_DELETED_MESSAGE matches this text to give the 409 its own error code."""
    assert kb_module.KB_BEING_DELETED_REASON == "This knowledge base is being deleted, so it can't be changed."


# ---- two deleters writing the marker at once (T42g) -----------------------------------------------------------------

CONFLICT = RuntimeError("write-write conflict")


def conflicting(service: KnowledgeBaseService, *, statuses: list[str | None], fail_updates: int) -> AsyncMock:
    """The KB is read as each of *statuses* in turn; the first *fail_updates* writes lose a conflict."""
    reads = iter(statuses)
    g = service.graph_provider
    g.get_document = AsyncMock(side_effect=lambda *a, **k: {"status": next(reads)})
    g.is_write_conflict = MagicMock(side_effect=lambda e: e is CONFLICT)
    outcomes = [CONFLICT] * fail_updates + [True]
    g.update_knowledge_base = AsyncMock(side_effect=outcomes)
    return g.update_knowledge_base


@pytest.mark.asyncio
async def test_a_deleter_that_lost_the_marker_write_to_a_rival_takes_the_rivals_marker(service) -> None:
    update = conflicting(service, statuses=[None, APP_STATUS_DELETING], fail_updates=1)

    assert await service._mark_deleting("kb1") == (APP_STATUS_DELETING,)
    assert update.await_count == 1


@pytest.mark.asyncio
async def test_a_lost_marker_write_is_written_again_when_nobody_else_marked(service) -> None:
    update = conflicting(service, statuses=[None, None], fail_updates=1)

    assert await service._mark_deleting("kb1") == (None,)
    assert update.await_count == 2


@pytest.mark.asyncio
async def test_a_marker_write_that_keeps_losing_fails_instead_of_looping(service) -> None:
    update = conflicting(service, statuses=[None] * 10, fail_updates=10)

    with pytest.raises(RuntimeError):
        await service._mark_deleting("kb1")
    assert update.await_count == 5


@pytest.mark.asyncio
async def test_another_error_in_the_marker_write_is_not_retried(service) -> None:
    arrange(service, status=None)
    service.graph_provider.update_knowledge_base = AsyncMock(side_effect=ValueError("bad"))
    with pytest.raises(ValueError, match="bad"):
        await service._mark_deleting("kb1")
    assert service.graph_provider.update_knowledge_base.await_count == 1


@pytest.mark.asyncio
async def test_a_kb_that_is_gone_is_not_marked(service) -> None:
    service.graph_provider.get_document = AsyncMock(return_value=None)
    assert await service._mark_deleting("kb1") is None
    service.graph_provider.update_knowledge_base.assert_not_awaited()


# ---- a rename that loses a write-write conflict to a push or a delete (T42r) ---------------------------------------

@pytest.mark.asyncio
async def test_a_rename_that_loses_a_write_conflict_writes_again(service) -> None:
    arrange(service, status=None)
    g = service.graph_provider
    g.is_write_conflict = MagicMock(side_effect=lambda e: e is CONFLICT)
    g.update_knowledge_base = AsyncMock(side_effect=[CONFLICT, CONFLICT, True])

    result = await service.update_knowledge_base(**ARGS["update_knowledge_base"])

    assert result["success"] is True and result["code"] == 200
    assert g.update_knowledge_base.await_count == 3


@pytest.mark.asyncio
async def test_a_rename_that_keeps_losing_fails_after_five_attempts(service) -> None:
    arrange(service, status=None)
    g = service.graph_provider
    g.is_write_conflict = MagicMock(side_effect=lambda e: e is CONFLICT)
    g.update_knowledge_base = AsyncMock(side_effect=[CONFLICT] * 10)

    result = await service.update_knowledge_base(**ARGS["update_knowledge_base"])

    assert result["code"] == 500
    assert g.update_knowledge_base.await_count == 5


@pytest.mark.asyncio
async def test_a_rename_error_that_is_not_a_conflict_is_not_retried(service) -> None:
    arrange(service, status=None)
    g = service.graph_provider
    g.is_write_conflict = MagicMock(return_value=False)
    g.update_knowledge_base = AsyncMock(side_effect=ValueError("bad"))

    assert (await service.update_knowledge_base(**ARGS["update_knowledge_base"]))["code"] == 500
    assert g.update_knowledge_base.await_count == 1
