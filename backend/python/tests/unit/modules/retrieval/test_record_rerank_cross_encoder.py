from __future__ import annotations

from typing import TYPE_CHECKING, Any

import fastembed.rerank.cross_encoder as fastembed_cross_encoder
import pytest

from app.modules.retrieval.record_rerank import (
    RecordCandidate,
    RecordRerankError,
    RerankerDeps,
    RerankRequest,
    create_record_reranker,
)
from app.modules.retrieval.record_rerank import cross_encoder as cross_encoder_mod
from app.modules.retrieval.record_rerank.cross_encoder import (
    DEFAULT_MODEL,
    DEFAULT_MODEL_FILE,
    CrossEncoderReranker,
    build_model,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


class FakeModel:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.calls: list[tuple[str, list[str]]] = []

    def rerank(self, query: str, documents: list[str], batch_size: int = 64) -> list[float]:
        self.calls.append((query, documents))
        return [self.scores.get(document, 0.0) for document in documents]


def _request(top: int = 2) -> RerankRequest:
    return RerankRequest(
        question="who signs off the depot roof work?",
        candidates=[
            RecordCandidate(record_id="r1", title="Canteen menu", text="Lunch options for March."),
            RecordCandidate(record_id="r2", title="Depot roof", text="Sign-off by the facilities lead."),
            RecordCandidate(record_id="r3", title="Roof survey", text=""),
        ],
        top=top,
        org_id="org",
    )


@pytest.fixture(autouse=True)
def _no_loaded_models() -> Iterator[None]:
    cross_encoder_mod._models.clear()
    yield
    cross_encoder_mod._models.clear()


@pytest.fixture
def model(monkeypatch) -> FakeModel:
    fake = FakeModel({"Depot roof\nSign-off by the facilities lead.": 7.5, "Roof survey": 1.0, "Canteen menu\nLunch options for March.": -6.0})
    monkeypatch.setattr(cross_encoder_mod, "build_model", lambda name: fake)
    return fake


class TestStrategy:
    def test_is_registered_and_uses_the_default_model(self, monkeypatch) -> None:
        monkeypatch.delenv("RECORD_RERANK_CROSS_ENCODER_MODEL", raising=False)
        reranker = create_record_reranker("cross_encoder", RerankerDeps())
        assert isinstance(reranker, CrossEncoderReranker)
        assert reranker.name == "cross_encoder"
        assert reranker._model_name == DEFAULT_MODEL

    def test_the_model_can_be_changed(self, monkeypatch) -> None:
        monkeypatch.setenv("RECORD_RERANK_CROSS_ENCODER_MODEL", " BAAI/bge-reranker-v2-m3 ")
        assert CrossEncoderReranker(RerankerDeps())._model_name == "BAAI/bge-reranker-v2-m3"

    async def test_ranks_by_score_and_keeps_the_top(self, model: FakeModel) -> None:
        assert await CrossEncoderReranker(RerankerDeps()).rank(_request(top=2)) == ["r2", "r3"]
        assert model.calls == [(
            "who signs off the depot roof work?",
            ["Canteen menu\nLunch options for March.", "Depot roof\nSign-off by the facilities lead.", "Roof survey"],
        )]

    async def test_equal_scores_keep_the_search_order(self, monkeypatch) -> None:
        monkeypatch.setattr(cross_encoder_mod, "build_model", lambda name: FakeModel({}))
        assert await CrossEncoderReranker(RerankerDeps()).rank(_request(top=3)) == ["r1", "r2", "r3"]

    async def test_no_candidates_loads_no_model(self, monkeypatch) -> None:
        def fail(name: str) -> None:
            raise AssertionError("loaded")

        monkeypatch.setattr(cross_encoder_mod, "build_model", fail)
        request = RerankRequest(question="q", candidates=[], top=8, org_id="org")
        assert await CrossEncoderReranker(RerankerDeps()).rank(request) == []

    async def test_a_short_score_list_is_an_error(self, monkeypatch) -> None:
        class Short(FakeModel):
            def rerank(self, query: str, documents: list[str], batch_size: int = 64) -> list[float]:
                return [1.0]

        monkeypatch.setattr(cross_encoder_mod, "build_model", lambda name: Short({}))
        with pytest.raises(RecordRerankError):
            await CrossEncoderReranker(RerankerDeps()).rank(_request())


class TestLoading:
    async def test_one_load_per_model_and_process(self, monkeypatch) -> None:
        built: list[str] = []

        def build(name: str) -> FakeModel:
            built.append(name)
            return FakeModel({})

        monkeypatch.setattr(cross_encoder_mod, "build_model", build)
        await CrossEncoderReranker(RerankerDeps()).rank(_request())
        await CrossEncoderReranker(RerankerDeps()).rank(_request())
        assert built == [DEFAULT_MODEL]

    async def test_a_failed_load_is_retried_by_the_next_search(self, monkeypatch) -> None:
        attempts: list[str] = []

        def build(name: str) -> FakeModel:
            attempts.append(name)
            if len(attempts) == 1:
                raise OSError("no network")
            return FakeModel({})

        monkeypatch.setattr(cross_encoder_mod, "build_model", build)
        with pytest.raises(OSError):
            await CrossEncoderReranker(RerankerDeps()).rank(_request())
        assert await CrossEncoderReranker(RerankerDeps()).rank(_request()) == ["r1", "r2"]
        assert len(attempts) == 2


class FakeTextCrossEncoder:
    supported: list[dict[str, Any]] = []
    added: list[tuple[str, str]] = []
    cached: set[str] = set()
    created: list[tuple[str, bool]] = []

    def __init__(self, model_name: str, local_files_only: bool = False) -> None:
        self.created.append((model_name, local_files_only))
        if local_files_only and model_name not in self.cached:
            raise ValueError("not cached")

    @classmethod
    def list_supported_models(cls) -> list[dict[str, Any]]:
        return cls.supported

    @classmethod
    def add_custom_model(cls, model: str, sources: object, model_file: str) -> None:
        cls.added.append((model, model_file))
        cls.supported.append({"model": model})


class TestBuildModel:
    @pytest.fixture(autouse=True)
    def _fake_fastembed(self, monkeypatch) -> None:
        FakeTextCrossEncoder.supported = [{"model": "BAAI/bge-reranker-v2-m3"}]
        FakeTextCrossEncoder.added = []
        FakeTextCrossEncoder.cached = set()
        FakeTextCrossEncoder.created = []
        monkeypatch.setattr(fastembed_cross_encoder, "TextCrossEncoder", FakeTextCrossEncoder)
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    def test_the_default_model_is_registered_once_with_its_int8_file(self) -> None:
        build_model(DEFAULT_MODEL)
        build_model(DEFAULT_MODEL)
        assert FakeTextCrossEncoder.added == [(DEFAULT_MODEL, DEFAULT_MODEL_FILE)]

    def test_a_fastembed_model_is_not_registered(self) -> None:
        FakeTextCrossEncoder.cached = {"BAAI/bge-reranker-v2-m3"}
        build_model("BAAI/bge-reranker-v2-m3")
        assert FakeTextCrossEncoder.added == []
        assert FakeTextCrossEncoder.created == [("BAAI/bge-reranker-v2-m3", True)]

    def test_another_hub_model_uses_the_standard_onnx_file(self) -> None:
        build_model("someone/reranker")
        assert FakeTextCrossEncoder.added == [("someone/reranker", "onnx/model.onnx")]

    def test_the_cache_is_tried_before_a_download(self) -> None:
        build_model(DEFAULT_MODEL)
        assert FakeTextCrossEncoder.created == [(DEFAULT_MODEL, True), (DEFAULT_MODEL, False)]

    def test_offline_without_a_cached_model_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        with pytest.raises(ValueError):
            build_model(DEFAULT_MODEL)
        assert FakeTextCrossEncoder.created == [(DEFAULT_MODEL, True)]
