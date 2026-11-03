"""An embedding-model change on a provider whose org spaces are tied to the model that first wrote to them (pgvector): refused while the
org holds a point, allowed (read only) while it holds none; the full provider:model identity counts as the model."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes.health import (
    EMBEDDING_CHANGE_UNSUPPORTED_CODE,
    _identity_of,
    handle_model_change,
)
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
    svc = _service(holds_points=True)
    await handle_model_change(svc, "m1", "m2", 0, 0, 512, MagicMock())     # nothing indexed yet: no space
    await handle_model_change(svc, "m1", "m1", 768, 40, 768, MagicMock())  # same model and size
    svc.vector_db_service.org_holds_points.assert_not_awaited()


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
