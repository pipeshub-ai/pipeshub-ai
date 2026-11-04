"""An embedding-model change on a provider whose org spaces are tied to the model that first wrote to them (pgvector): refused while the
org holds a point, allowed (read only) while it holds none; the full provider:model identity counts as the model."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes.health import (
    EMBEDDING_CHANGE_UNSUPPORTED_CODE,
    _identity_of,
    check_collection_info,
    handle_model_change,
)
from app.core import org_context
from app.services.vector_db.collections import CollectionType
from app.services.vector_db.models import VectorDBCapabilities


def _service(spaces_per_org: bool = True, holds_points: bool = False, accepts: bool = False) -> MagicMock:
    svc = MagicMock()
    svc.vector_db_service.get_capabilities.return_value = VectorDBCapabilities(spaces_per_org=spaces_per_org)
    svc.vector_db_service.org_holds_points = AsyncMock(return_value=holds_points)
    svc.vector_db_service.org_space_accepts = AsyncMock(return_value=accepts)
    return svc


async def test_an_org_that_holds_no_point_may_change_model_and_nothing_is_written() -> None:
    svc = _service(holds_points=False)
    await handle_model_change(svc, "m1", "M2", 768, 0, 512, MagicMock())
    svc.vector_db_service.org_holds_points.assert_awaited_once()
    assert not [c for c in svc.vector_db_service.method_calls if "replace" in c[0] or "create" in c[0] or "delete" in c[0]]


async def test_an_org_that_holds_a_point_is_refused_with_the_stores_own_text() -> None:
    svc = _service(holds_points=True)
    with pytest.raises(HTTPException) as exc:
        await handle_model_change(svc, "m1", "m2", 768, 0, 512, MagicMock())      # no record point left, an entity point still there
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == EMBEDDING_CHANGE_UNSUPPORTED_CODE and exc.value.detail["status"] == "not healthy"
    assert "pgvector" in exc.value.detail["error"]


async def test_going_back_to_the_model_the_space_already_takes_is_always_allowed() -> None:
    """The admin is never stuck: an old-model write that claimed the space must not make the way back refuse."""
    svc = _service(holds_points=True, accepts=True)
    await handle_model_change(svc, "m2", "m1", 768, 40, 768, MagicMock(), current_identity="p:m2", new_identity="p:m1")
    svc.vector_db_service.org_space_accepts.assert_awaited_once_with("p:m1:768", 768, "p:m2:768")


async def test_a_dimension_change_is_refused_the_same_way() -> None:
    with pytest.raises(HTTPException) as exc:
        await handle_model_change(_service(holds_points=True), "m1", "m1", 768, 40, 512, MagicMock())
    assert exc.value.detail["code"] == EMBEDDING_CHANGE_UNSUPPORTED_CODE


async def test_a_provider_switch_for_the_same_model_is_a_change() -> None:
    with pytest.raises(HTTPException) as exc:
        await handle_model_change(_service(holds_points=True), "m1", "m1", 768, 40, 768, MagicMock(),
                                  current_identity="openai:m1", new_identity="azure:m1")
    assert exc.value.detail["code"] == EMBEDDING_CHANGE_UNSUPPORTED_CODE
    await handle_model_change(_service(holds_points=True), "m1", "m1", 768, 40, 768, MagicMock(),
                              current_identity="openai:m1", new_identity="openai:m1")      # same identity: nothing changes


async def test_an_org_without_a_space_and_an_unchanged_model_pass() -> None:
    svc = _service(holds_points=False)
    await handle_model_change(svc, "m1", "m2", 0, 0, 512, MagicMock())     # nothing indexed yet: no space, no point
    svc.vector_db_service.org_holds_points.assert_awaited_once()
    await handle_model_change(svc, "m1", "m1", 768, 40, 768, MagicMock(), current_identity="p:m1", new_identity="p:m1")
    svc.vector_db_service.org_holds_points.assert_awaited_once()           # a known, unchanged model and size is never asked


async def test_other_stores_keep_their_behaviour(monkeypatch) -> None:
    from app.api.routes import health

    recreate = AsyncMock()
    monkeypatch.setattr(health, "recreate_collection", recreate)
    svc = _service(spaces_per_org=False)
    await handle_model_change(svc, "m1", "m2", 768, 0, 512, MagicMock())
    recreate.assert_awaited_once()
    with pytest.raises(HTTPException) as exc:
        await handle_model_change(svc, "m1", "m2", 768, 5, 512, MagicMock())
    assert "code" not in exc.value.detail
    await handle_model_change(svc, "m1", "m1", 768, 5, 768, MagicMock(), current_identity="a:m1", new_identity="b:m1")   # no new refusal elsewhere


def test_identity_is_provider_and_the_first_model() -> None:
    assert _identity_of({"provider": "openai", "configuration": {"model": " a , b"}}) == "openai:a"
    assert _identity_of({"configuration": {"model": "a"}}) is None


async def test_with_no_org_bound_the_check_fails_closed_instead_of_seeing_an_empty_store() -> None:
    """The provider's survey is the calling org's: with none bound it sees no space and no point, which read as "nothing
    indexed" and allowed the change."""
    svc = _service(holds_points=True)
    svc.collection_registry.list_managed_collections = AsyncMock(
        return_value=[SimpleNamespace(name="records", collection_type=CollectionType.RECORDS.value)]
    )
    svc.vector_db_service.get_collection_info = AsyncMock(return_value=MagicMock(exists=True, dense_dimension=0, points_count=0))
    svc.get_current_embedding_model_name = AsyncMock(return_value="m1")
    svc.get_embedding_model_name = MagicMock(return_value="m2")

    with pytest.raises(HTTPException) as exc:
        await check_collection_info(svc, MagicMock(), 512, MagicMock())

    assert exc.value.status_code == 503
    svc.vector_db_service.org_holds_points.assert_not_awaited()


async def test_with_an_org_bound_the_check_goes_on() -> None:
    svc = _service(holds_points=False)
    svc.collection_registry.list_managed_collections = AsyncMock(
        return_value=[SimpleNamespace(name="records", collection_type=CollectionType.RECORDS.value)]
    )
    svc.vector_db_service.get_collection_info = AsyncMock(return_value=MagicMock(exists=True, dense_dimension=0, points_count=0))
    svc.get_current_embedding_model_name = AsyncMock(return_value="m1")
    svc.get_embedding_model_name = MagicMock(return_value="m1")

    with org_context.for_org("org-1", job="test"):
        await check_collection_info(svc, MagicMock(), 512, MagicMock())


@pytest.mark.parametrize("failing", ["org_holds_points", "org_space_accepts"])
async def test_a_database_error_reading_the_org_space_fails_closed(failing: str) -> None:
    """A transient read error must not let a change the space does not accept through (it would fail later with SpaceMismatch)."""
    from app.api.routes.health import CollectionSurveyError

    svc = _service(holds_points=True)
    setattr(svc.vector_db_service, failing, AsyncMock(side_effect=ConnectionError("db down")))
    with pytest.raises(CollectionSurveyError):
        await handle_model_change(svc, "m1", "m2", 768, 40, 512, MagicMock())


async def test_an_org_with_entity_points_and_no_records_collection_is_refused_a_model_change() -> None:
    """No managed records collection means no dimension from the survey, but the org's entity points still pin its space."""
    with pytest.raises(HTTPException) as exc:
        await handle_model_change(_service(holds_points=True), "m1", "m2", 0, 0, 512, MagicMock())
    assert exc.value.status_code == 400 and exc.value.detail["code"] == EMBEDDING_CHANGE_UNSUPPORTED_CODE


async def test_an_org_with_nothing_at_all_may_change_model_without_a_records_collection() -> None:
    await handle_model_change(_service(holds_points=False), "m1", "m2", 0, 0, 512, MagicMock())


async def test_a_dimension_change_is_refused_when_the_records_survey_sees_no_dimension() -> None:
    """Same model name, no records collection (dimension 0), entity points at another dimension: the space refuses."""
    svc = _service(holds_points=True, accepts=False)
    with pytest.raises(HTTPException) as exc:
        await handle_model_change(svc, "m1", "m1", 0, 0, 512, MagicMock(), current_identity="p:m1", new_identity="p:m1")
    assert exc.value.detail["code"] == EMBEDDING_CHANGE_UNSUPPORTED_CODE


async def test_a_same_model_provider_change_is_refused_when_the_current_identity_could_not_be_read() -> None:
    svc = _service(holds_points=True, accepts=False)
    with pytest.raises(HTTPException) as exc:
        await handle_model_change(svc, "m1", "m1", 768, 40, 768, MagicMock(), current_identity=None, new_identity="azure:m1")
    assert exc.value.detail["code"] == EMBEDDING_CHANGE_UNSUPPORTED_CODE


async def test_an_unchanged_config_that_the_space_accepts_passes_and_rebuilds_nothing() -> None:
    svc = _service(holds_points=True, accepts=True)
    await handle_model_change(svc, "m1", "m1", 0, 0, 768, MagicMock(), current_identity="p:m1", new_identity="p:m1")
    assert not [c for c in svc.vector_db_service.method_calls if "recreate" in c[0] or "delete" in c[0] or "create" in c[0]]
