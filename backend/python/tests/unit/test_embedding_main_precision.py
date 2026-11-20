"""The embedding server's weight precision: loading, fallback and reporting."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from safetensors.torch import save_file
from torch import nn

import app.utils.int8_file as int8_file
from app.embedding_main import ModelManager, app
from app.utils import embedding_precision as ep
from app.utils.embedding_precision import FP32, INT8, PrecisionSelector, ProbeResult
from app.utils.int8_linear import Int8WeightLinear


class _Encoder(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.embed = nn.Embedding(8, 16)
        self.dense = nn.Linear(16, 16)


class _FakeSentenceTransformer:
    """Just enough of a SentenceTransformer: ``model[0].auto_model`` is the transformer."""

    def __init__(self) -> None:
        self.auto_model = _Encoder()

    def __getitem__(self, index: int) -> "_FakeSentenceTransformer":
        return self


async def _inline(func, *args, **kwargs):  # noqa: ANN202
    return func(*args, **kwargs)


def _selector(tmp_path: Path, request: str, probe_result: ProbeResult | None = None) -> PrecisionSelector:
    return PrecisionSelector(
        state_path=tmp_path / "embedding_precision.json", quantizer_version="1", request=request,
        memtotal=8 * 1024**3, probe=lambda: probe_result or ProbeResult("pass", "ok"),
    )


async def _load(
    selector: PrecisionSelector, models: list, tmp_path: Path | None = None, *, hub: Path | None = None, build_effect=int8_file.build,
) -> tuple[ModelManager, object]:
    def make(*_a, **_k):  # noqa: ANN202
        models.append(_FakeSentenceTransformer())
        if hub is not None:
            save_file({k: v.contiguous() for k, v in models[-1].auto_model.state_dict().items()},
                      str(hub / "models--m" / "snapshots" / "rev1" / "model.safetensors"))
        return models[-1]

    manager = ModelManager(device="cpu")
    with (
        patch("app.embedding_main.precision_selector", selector),
        patch("sentence_transformers.SentenceTransformer", side_effect=make),
        patch("app.embedding_main.asyncio.to_thread", side_effect=_inline),
        patch.object(ModelManager, "_build_counter", side_effect=RuntimeError("no tokenizer")),
        patch("huggingface_hub.hf_hub_download", side_effect=lambda *a, **k: str(hub / "models--m" / "snapshots" / "rev1" / "model.safetensors")),
        patch("huggingface_hub.constants.HF_HUB_CACHE", str(hub) if hub else "/nonexistent-cache"),
        patch.object(int8_file, "build_in_child", side_effect=build_effect),
    ):
        model = await manager.get_model("m")
    return manager, model


class TestLoading:
    async def test_auto_keeps_every_weight_in_fp32(self, tmp_path) -> None:
        models: list = []
        manager, model = await _load(_selector(tmp_path, "auto"), models)
        assert isinstance(model.auto_model.dense, nn.Linear)
        assert manager.precision_of("m").precision == FP32
        assert len(models) == 1

    async def test_int8_backs_the_linears_with_the_file_and_keeps_the_table(self, tmp_path) -> None:
        hub = tmp_path / "hub"
        (hub / "models--m" / "snapshots" / "rev1").mkdir(parents=True)
        manager, model = await _load(_selector(tmp_path, "int8"), [], hub=hub)
        assert isinstance(model.auto_model.dense, Int8WeightLinear)
        assert isinstance(model.auto_model.embed, nn.Embedding)
        assert manager.precision_of("m").precision == INT8
        assert any((hub / "int8-weights").glob("*.safetensors"))

    async def test_an_unwritable_cache_serves_fp32_and_says_why(self, tmp_path) -> None:
        hub = tmp_path / "hub"
        (hub / "models--m" / "snapshots" / "rev1").mkdir(parents=True)
        (hub / "int8-weights").mkdir()
        (hub / "int8-weights").chmod(0o500)
        models: list = []
        try:
            manager, model = await _load(_selector(tmp_path, "int8"), models, hub=hub)
        finally:
            (hub / "int8-weights").chmod(0o700)
        decision = manager.precision_of("m")
        assert len(models) == 1 and isinstance(model.auto_model.dense, nn.Linear)
        assert decision.precision == FP32 and "not writable" in decision.reason
        assert '"decision": "int8-w"' in (tmp_path / "embedding_precision.json").read_text()

    async def test_a_failed_conversion_serves_fp32_and_keeps_the_saved_decision(self, tmp_path) -> None:
        hub = tmp_path / "hub"
        (hub / "models--m" / "snapshots" / "rev1").mkdir(parents=True)
        models: list = []
        manager, model = await _load(
            _selector(tmp_path, "int8"), models, hub=hub, build_effect=int8_file.Int8FileError("converter died"),
        )
        assert isinstance(model.auto_model.dense, nn.Linear)
        decision = manager.precision_of("m")
        assert decision.precision == FP32 and "converter died" in decision.reason
        assert '"decision": "int8-w"' in (tmp_path / "embedding_precision.json").read_text()

    async def test_a_model_without_linears_is_not_reported_as_int8(self, tmp_path) -> None:
        hub = tmp_path / "hub"
        (hub / "models--m" / "snapshots" / "rev1").mkdir(parents=True)
        with patch.object(int8_file, "eligible_linears", return_value={}):
            manager, _ = await _load(_selector(tmp_path, "int8"), [], hub=hub)
        assert manager.precision_of("m").precision == FP32

    async def test_the_idle_reaper_forgets_the_precision(self, tmp_path) -> None:
        hub = tmp_path / "hub"
        (hub / "models--m" / "snapshots" / "rev1").mkdir(parents=True)
        manager, _ = await _load(_selector(tmp_path, "int8"), [], hub=hub)
        manager._idle_seconds = 1
        assert manager.reap_idle(now=1e9) == ["m"]
        assert manager.precision_of("m") is None


@pytest.fixture(autouse=True)
def _no_settings_lookup_at_startup():  # noqa: ANN202
    with patch("app.embedding_main._local_model_in_use", new_callable=AsyncMock, return_value=False):
        yield


class TestReporting:
    def _client(self, precision) -> tuple[TestClient, MagicMock]:
        manager = MagicMock(spec=ModelManager)
        manager.list_loaded_models.return_value = ["BAAI/bge-large-en-v1.5"]
        manager.max_seq_length.return_value = 512
        manager.prompt_tokens.return_value = 0
        manager.precision_of.return_value = precision
        manager.encode_gate_stats.return_value = {}
        manager.warmup = AsyncMock()
        return manager

    def test_models_and_health_report_the_precision_and_reason(self, tmp_path) -> None:
        selector = _selector(tmp_path, "int8")
        decision = selector.decide()
        manager = self._client(decision)
        with (
            patch("app.embedding_main.model_manager", manager),
            patch("app.embedding_main.precision_selector", selector),
            TestClient(app) as client,
        ):
            item = client.get("/v1/models").json()["data"][0]
            health = client.get("/health").json()
        for body in (item, health):
            assert body["precision"] == "int8-w"
            assert body["quantizer_version"] == "1"
            assert "probe pass" in body["precision_reason"]
        assert item["id"] == "BAAI/bge-large-en-v1.5" and item["object"] == "model"

    def test_nothing_is_reported_before_a_model_is_loaded(self) -> None:
        manager = self._client(None)
        manager.list_loaded_models.return_value = []
        with (
            patch("app.embedding_main.model_manager", manager),
            patch("app.embedding_main.precision_selector", PrecisionSelector(
                state_path=Path("/nonexistent/x.json"), quantizer_version="1")),
            TestClient(app) as client,
        ):
            health = client.get("/health").json()
            item = client.get("/v1/models").json()["data"][0]
        assert health["precision"] is None and item["precision"] is None

    def test_the_decision_is_logged_once(self, tmp_path) -> None:
        selector = _selector(tmp_path, "int8")
        with patch.object(ep, "logger") as log:
            selector.decide()
            selector.decide()
        assert log.info.call_count == 1


class TestPrecisionHeaders:
    """Each embeddings response names the weights of the model object that computed it."""

    @staticmethod
    def _post(decision) -> object:
        manager = MagicMock(spec=ModelManager)

        async def encode(model, texts, *, witness=None, **_kwargs):  # noqa: ANN202
            if witness is not None:
                witness["decision"] = decision
            return [[0.1, 0.2] for _ in texts]

        manager.encode = encode
        with patch("app.embedding_main.model_manager", manager), TestClient(app) as client:
            return client.post("/v1/embeddings", json={"model": "m", "input": "hello"})

    def test_the_header_comes_from_the_model_that_encoded(self) -> None:
        response = self._post(ep.PrecisionDecision(INT8, "ok", "1", INT8))
        assert response.headers["x-embedding-precision"] == "int8-w"
        assert "x-embedding-precision-fallback" not in response.headers
        assert response.json()["data"][0]["embedding"] == [0.1, 0.2]

    def test_a_fallback_is_announced_with_the_precision(self) -> None:
        response = self._post(ep.PrecisionDecision(FP32, "int8 load failed", "1", INT8))
        assert response.headers["x-embedding-precision"] == "fp32"
        assert response.headers["x-embedding-precision-fallback"] == "1"

    def test_no_header_when_the_model_reports_nothing(self) -> None:
        response = self._post(None)
        assert "x-embedding-precision" not in response.headers

    def test_decisions_know_their_intent_and_fallback(self) -> None:
        assert ep.PrecisionDecision(FP32, "x", "1", INT8).fallback is True
        assert ep.PrecisionDecision(INT8, "x", "1", INT8).fallback is False
        assert ep.PrecisionDecision(FP32, "x", "1").fallback is False

    def test_the_model_object_carries_the_decision_it_was_loaded_with(self) -> None:
        from app.embedding_main import _stamp_precision

        decision = ep.PrecisionDecision(INT8, "ok", "1", INT8)
        model = _FakeSentenceTransformer()
        _stamp_precision(model, decision)
        assert model._pipeshub_precision is decision


class TestIntent:
    def test_auto_is_fp32_on_every_host_while_the_threshold_is_zero(self) -> None:
        assert ep.AUTO_INT8_BELOW_MEMTOTAL_BYTES == 0
        for memtotal in (1, 4 * 1024**3, 64 * 1024**3, None):
            selector = PrecisionSelector(
                state_path=Path("/nonexistent/x.json"), quantizer_version="1", request="auto", memtotal=memtotal,
            )
            assert selector.intent() == FP32

    def test_explicit_requests_and_devices(self) -> None:
        def intent(request, device="cpu"):  # noqa: ANN202
            return PrecisionSelector(
                state_path=Path("/nonexistent/x.json"), quantizer_version="1", request=request, memtotal=1,
            ).intent(device)

        assert intent("int8") == INT8 and intent("fp32") == FP32 and intent("int8", "cuda") == FP32


class TestWitnessFromTheModelThatEncoded:
    @pytest.mark.asyncio
    async def test_the_decision_handed_back_is_the_one_on_the_model_object_that_did_the_work(self) -> None:
        import contextlib

        import numpy as np

        decision = ep.PrecisionDecision(INT8, "ok", "1", INT8)
        other = ep.PrecisionDecision(FP32, "a reload decided again", "1", INT8)

        class _Model:
            _pipeshub_precision = decision

            def encode(self, texts, **_kwargs):  # noqa: ANN202
                return np.array([[0.1, 0.2] for _ in texts])

        manager = ModelManager(device="cpu")
        manager._precisions["m"] = other  # what a lookup by name would say after a reload
        manager._in_use["m"] = 0

        @contextlib.asynccontextmanager
        async def lease(_name, **_kwargs):  # noqa: ANN202
            yield _Model()

        manager._lease = lease
        witness: dict = {}
        vectors = await manager.encode("m", ["a", "b"], witness=witness)
        assert len(vectors) == 2 and witness["decision"] is decision
