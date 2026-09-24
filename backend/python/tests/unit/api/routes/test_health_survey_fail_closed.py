"""The embedding-model guard must fail closed.

`check_collection_info` decides whether an embedding-model change is allowed,
and `recreate_collection` then *drops* collections. Treating "could not read
the index state" as "there is no index state" is a fail-open on a destructive
operation: the change is accepted, the collections are dropped, and the corpus
has to be re-indexed from source. Refusing costs a retry.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes.health import (
    CollectionSurveyError,
    check_collection_info,
    recreate_collection,
    survey_managed_collections,
)
from app.services.vector_db.models import VectorCollectionInfo
from tests.support.vector_db import make_collection_registry


def _retrieval_service(*, registry=None, info=None, info_error=None):
    svc = AsyncMock()
    svc.collection_registry = registry or make_collection_registry("records")
    if info_error is not None:
        svc.vector_db_service.get_collection_info = AsyncMock(side_effect=info_error)
    else:
        svc.vector_db_service.get_collection_info = AsyncMock(
            return_value=info
            or VectorCollectionInfo(
                name="records", exists=True, dense_dimension=1024, points_count=0
            )
        )
    svc.get_current_embedding_model_name = AsyncMock(return_value="model-a")
    svc.get_embedding_model_name = MagicMock(return_value="model-b")
    return svc


class TestSurveyPropagatesFailure:
    @pytest.mark.asyncio
    async def test_unreadable_collection_info_raises_survey_error(self):
        svc = _retrieval_service(info_error=ConnectionError("vector db down"))

        with pytest.raises(CollectionSurveyError):
            await survey_managed_collections(svc, MagicMock())

    @pytest.mark.asyncio
    async def test_unreadable_manifest_raises_survey_error(self):
        registry = make_collection_registry("records")
        registry.list_managed_collections = AsyncMock(
            side_effect=ConnectionError("kv down")
        )
        svc = _retrieval_service(registry=registry)

        with pytest.raises(CollectionSurveyError):
            await survey_managed_collections(svc, MagicMock())

    @pytest.mark.asyncio
    async def test_reads_the_manifest_fresh(self):
        """A cached view could miss a collection another service created since
        this process started, and the guard would wave the change through."""
        registry = make_collection_registry("records")
        svc = _retrieval_service(registry=registry)

        await survey_managed_collections(svc, MagicMock())

        registry.list_managed_collections.assert_awaited_once_with(fresh=True)


class TestGuardFailsClosed:
    @pytest.mark.asyncio
    async def test_survey_failure_rejects_the_model_change(self):
        """Previously swallowed as a warning, letting the change proceed."""
        svc = _retrieval_service(info_error=ConnectionError("vector db down"))

        with pytest.raises(HTTPException) as exc:
            await check_collection_info(svc, MagicMock(), 1024, MagicMock())

        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_populated_collection_still_rejects_with_400(self):
        svc = _retrieval_service(
            info=VectorCollectionInfo(
                name="records", exists=True, dense_dimension=768, points_count=42
            )
        )

        with pytest.raises(HTTPException) as exc:
            await check_collection_info(svc, MagicMock(), 1024, MagicMock())

        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_empty_collection_allows_the_change(self):
        svc = _retrieval_service(
            info=VectorCollectionInfo(
                name="records", exists=True, dense_dimension=768, points_count=0
            )
        )

        await check_collection_info(svc, MagicMock(), 1024, MagicMock())

        svc.collection_registry.recreate_all_collections.assert_awaited_once()


class TestEmptyManifestRebuild:
    @pytest.mark.asyncio
    async def test_nothing_managed_creates_nothing(self):
        """Creating one here would have to invent a context; under a per-org or
        per-connector strategy that names a collection belonging to nobody."""
        registry = make_collection_registry("records")
        registry.recreate_all_collections = AsyncMock(return_value=[])
        svc = _retrieval_service(registry=registry)

        await recreate_collection(svc, 1024, MagicMock())

        registry.ensure_collection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rebuild_failure_propagates(self):
        registry = make_collection_registry("records")
        registry.recreate_all_collections = AsyncMock(side_effect=RuntimeError("boom"))
        svc = _retrieval_service(registry=registry)

        with pytest.raises(RuntimeError):
            await recreate_collection(svc, 1024, MagicMock())
