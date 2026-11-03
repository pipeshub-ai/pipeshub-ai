"""Tests for the Qdrant memory estimate and the threshold that raises the warning."""

import copy
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.vector_db.qdrant.memory_sizing import (
    BASE_BYTES,
    GIB,
    MIN_INDEX_BYTES_TO_WARN,
    SOURCE_CONFIGURED,
    SOURCE_HOST,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    STATUS_UNKNOWN,
    assess_memory,
    collection_footprint,
    node_share,
    parse_memory_size,
)

# GET /collections/records on a 5.08M-point collection, trimmed to the fields
# the estimate reads. Files on disk for these parts: quantized vectors 8.33e9 B,
# keyword payload indexes 1.33e9 B, HNSW 0.30e9 B, sparse index 0.24e9 B.
LARGE_COLLECTION = {
    "points_count": 5082556,
    "config": {
        "params": {
            "vectors": {"dense": {"size": 1536, "distance": "Cosine", "on_disk": True}},
            "shard_number": 1,
            "replication_factor": 1,
            "sparse_vectors": {"sparse": {"index": {"on_disk": True}, "modifier": "idf"}},
        },
        "hnsw_config": {"m": 16},
        "quantization_config": {
            "scalar": {"type": "int8", "quantile": 0.95, "always_ram": False}
        },
    },
    "payload_schema": {
        "recordGroupIds": {"data_type": "keyword", "points": 5082506},
        "metadata.orgId": {"data_type": "keyword", "points": 5082556},
        "connectorIds": {"data_type": "keyword", "points": 5082556},
        "rootRecordGroupIds": {"data_type": "keyword", "points": 150000},
        "metadata.virtualRecordId": {"data_type": "keyword", "points": 5082556},
    },
}


def _collection(
    points: int, dimension: int = 1024, quantization: dict | None = None, **params: int
) -> dict:
    info = copy.deepcopy(LARGE_COLLECTION)
    info["points_count"] = points
    info["config"]["params"]["vectors"]["dense"]["size"] = dimension
    info["config"]["params"].update(params)
    if quantization is not None:
        info["config"]["quantization_config"] = quantization
    for schema in info["payload_schema"].values():
        schema["points"] = points
    return info


class TestParseMemorySize:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("4G", 4 * GIB),
            ("6g", 6 * GIB),
            ("8Gi", 8 * GIB),
            ("12GB", 12 * GIB),
            ("512m", 512 * 1024**2),
            ("4096Mi", 4 * GIB),
            ("1.5G", int(1.5 * GIB)),
            (" 2T ", 2 * 1024**4),
            ("6442450944", 6442450944),
        ],
    )
    def test_reads_docker_and_kubernetes_sizes(self, value, expected) -> None:
        assert parse_memory_size(value) == expected

    @pytest.mark.parametrize("value", [None, "", "  ", "lots", "4X", "-4G", "0", "G"])
    def test_rejects_what_is_not_a_size(self, value) -> None:
        assert parse_memory_size(value) is None


class TestCollectionFootprint:
    def test_large_collection_matches_files_on_disk(self) -> None:
        fp = collection_footprint("records", LARGE_COLLECTION)

        assert fp.points == 5082556
        assert fp.quantization == "scalar"
        assert fp.always_ram is False
        assert fp.quantized_vectors_bytes == 5082556 * (1536 + 4)
        assert fp.hnsw_bytes == 5082556 * 60
        assert fp.sparse_index_bytes == 5082556 * 50
        assert fp.payload_index_bytes == (5082506 + 3 * 5082556 + 150000) * 65
        # Within 5% of the measured 10.21e9 bytes, on the low side.
        on_disk = 8_333_677_660 + 1_334_957_180 + 297_276_584 + 242_166_749
        assert 0.95 * on_disk <= fp.index_bytes <= on_disk

    def test_empty_collection_needs_nothing(self) -> None:
        fp = collection_footprint("records", _collection(0))
        assert fp.index_bytes == 0

    def test_missing_fields_are_tolerated(self) -> None:
        fp = collection_footprint("x", {})
        assert fp.points == 0
        assert fp.index_bytes == 0
        assert fp.quantization == "none"

    def test_without_quantization_the_full_vectors_are_the_working_set(self) -> None:
        info = _collection(1000, dimension=1024)
        info["config"]["quantization_config"] = None
        fp = collection_footprint("x", info)
        assert fp.quantization == "none"
        assert fp.quantized_vectors_bytes == 1000 * 1024 * 4

    def test_product_quantization_uses_the_compression_ratio(self) -> None:
        info = _collection(1000, 1024, {"product": {"compression": "x16", "always_ram": True}})
        fp = collection_footprint("x", info)
        assert fp.quantization == "product"
        assert fp.always_ram is True
        assert fp.quantized_vectors_bytes == 1000 * 1024 * 4 // 16

    def test_binary_quantization_counts_bits(self) -> None:
        one_bit = collection_footprint("x", _collection(1000, 1536, {"binary": {}}))
        two_bits = collection_footprint(
            "x", _collection(1000, 1536, {"binary": {"encoding": "two_bits"}})
        )
        assert one_bit.quantized_vectors_bytes == 1000 * 1536 // 8
        assert two_bits.quantized_vectors_bytes == 1000 * 1536 // 4

    def test_per_vector_quantization_overrides_the_collection(self) -> None:
        info = _collection(1000, 1024)
        info["config"]["params"]["vectors"]["dense"]["quantization_config"] = {"binary": {}}
        fp = collection_footprint("x", info)
        assert fp.quantization == "binary"
        assert fp.quantized_vectors_bytes == 1000 * 1024 // 8

    def test_unnamed_vector_config(self) -> None:
        info = _collection(1000, 768)
        info["config"]["params"]["vectors"] = {"size": 768, "distance": "Cosine"}
        fp = collection_footprint("x", info)
        assert fp.quantized_vectors_bytes == 1000 * (768 + 4)

    def test_hnsw_scales_with_m_and_is_absent_at_zero(self) -> None:
        info = _collection(1000)
        info["config"]["hnsw_config"]["m"] = 32
        assert collection_footprint("x", info).hnsw_bytes == 1000 * 120
        info["config"]["hnsw_config"]["m"] = 0
        assert collection_footprint("x", info).hnsw_bytes == 0

    def test_no_sparse_vectors_means_no_sparse_index(self) -> None:
        info = _collection(1000)
        info["config"]["params"]["sparse_vectors"] = None
        assert collection_footprint("x", info).sparse_index_bytes == 0


class TestNodeShare:
    def test_single_node_holds_everything(self) -> None:
        assert node_share(LARGE_COLLECTION, 1) == 1

    def test_one_shard_on_two_nodes_still_sits_on_one_node(self) -> None:
        assert node_share(LARGE_COLLECTION, 2) == 1

    def test_shards_spread_over_nodes(self) -> None:
        info = _collection(10, shard_number=2, replication_factor=1)
        assert node_share(info, 2) == 0.5

    def test_replicas_add_copies(self) -> None:
        info = _collection(10, shard_number=3, replication_factor=2)
        assert node_share(info, 3) == pytest.approx(2 / 3)


class TestAssessMemory:
    def test_large_index_at_twelve_gib_is_ok(self) -> None:
        report = assess_memory(
            {"records": LARGE_COLLECTION},
            configured_limit_bytes=12 * GIB,
            host_ram_bytes=61 * GIB,
        )
        assert report.status == STATUS_OK
        assert report.available_bytes == 12 * GIB
        assert report.available_source == SOURCE_CONFIGURED
        assert report.needed_bytes == report.index_bytes + BASE_BYTES
        assert 10.9 * GIB < report.needed_bytes < 11.2 * GIB

    def test_large_index_at_four_gib_is_insufficient(self) -> None:
        report = assess_memory(
            {"records": LARGE_COLLECTION},
            configured_limit_bytes=4 * GIB,
            host_ram_bytes=61 * GIB,
        )
        assert report.status == STATUS_INSUFFICIENT
        assert report.available_bytes == 4 * GIB

    @pytest.mark.parametrize("points", [0, 10_000, 200_000, 1_000_000])
    def test_small_installs_at_the_shipped_four_gib_do_not_warn(self, points) -> None:
        report = assess_memory(
            {"records": _collection(points, 1024)},
            configured_limit_bytes=4 * GIB,
            host_ram_bytes=16 * GIB,
        )
        assert report.status == STATUS_OK

    def test_tiny_index_never_warns_even_under_a_tiny_limit(self) -> None:
        report = assess_memory(
            {"records": _collection(50_000, 1024)},
            configured_limit_bytes=1 * GIB,
            host_ram_bytes=8 * GIB,
        )
        assert report.index_bytes < MIN_INDEX_BYTES_TO_WARN
        assert report.needed_bytes > report.available_bytes
        assert report.status == STATUS_OK

    def test_threshold_is_the_available_memory(self) -> None:
        info = _collection(2_000_000, 1024)
        needed = assess_memory({"r": info}, configured_limit_bytes=None, host_ram_bytes=None).needed_bytes

        at = assess_memory({"r": info}, configured_limit_bytes=needed, host_ram_bytes=None)
        below = assess_memory({"r": info}, configured_limit_bytes=needed - 1, host_ram_bytes=None)
        assert at.status == STATUS_OK
        assert below.status == STATUS_INSUFFICIENT

    def test_host_ram_alone_can_prove_a_shortfall(self) -> None:
        report = assess_memory(
            {"records": LARGE_COLLECTION}, configured_limit_bytes=None, host_ram_bytes=8 * GIB
        )
        assert report.status == STATUS_INSUFFICIENT
        assert report.available_source == SOURCE_HOST

    def test_host_ram_alone_cannot_prove_enough(self) -> None:
        report = assess_memory(
            {"records": LARGE_COLLECTION}, configured_limit_bytes=None, host_ram_bytes=61 * GIB
        )
        assert report.status == STATUS_UNKNOWN
        assert report.available_bytes == 61 * GIB

    def test_host_ram_caps_a_larger_configured_limit(self) -> None:
        report = assess_memory(
            {"records": LARGE_COLLECTION},
            configured_limit_bytes=64 * GIB,
            host_ram_bytes=8 * GIB,
        )
        assert report.available_bytes == 8 * GIB
        assert report.available_source == SOURCE_HOST
        assert report.status == STATUS_INSUFFICIENT

    def test_nothing_known_is_unknown(self) -> None:
        report = assess_memory(
            {"records": LARGE_COLLECTION}, configured_limit_bytes=None, host_ram_bytes=None
        )
        assert report.status == STATUS_UNKNOWN
        assert report.available_bytes is None

    def test_no_collections(self) -> None:
        report = assess_memory({}, configured_limit_bytes=4 * GIB, host_ram_bytes=None)
        assert report.status == STATUS_OK
        assert report.needed_bytes == BASE_BYTES
        assert report.points == 0

    def test_collections_add_up(self) -> None:
        one = assess_memory({"a": LARGE_COLLECTION}, configured_limit_bytes=None, host_ram_bytes=None)
        two = assess_memory(
            {"a": LARGE_COLLECTION, "b": LARGE_COLLECTION},
            configured_limit_bytes=None,
            host_ram_bytes=None,
        )
        assert two.index_bytes == 2 * one.index_bytes
        assert two.points == 2 * one.points

    def test_sharded_collection_needs_less_per_node(self) -> None:
        info = _collection(5_000_000, 1536, shard_number=2, replication_factor=1)
        single = assess_memory({"r": info}, configured_limit_bytes=None, host_ram_bytes=None, nodes=1)
        spread = assess_memory({"r": info}, configured_limit_bytes=None, host_ram_bytes=None, nodes=2)
        assert spread.index_bytes == pytest.approx(single.index_bytes / 2, abs=1)
        assert spread.nodes == 2

    def test_to_dict_shape(self) -> None:
        report = assess_memory(
            {"records": LARGE_COLLECTION}, configured_limit_bytes=4 * GIB, host_ram_bytes=None
        ).to_dict()
        assert report["status"] == STATUS_INSUFFICIENT
        assert set(report) == {
            "status", "neededBytes", "indexBytes", "baseBytes", "availableBytes",
            "availableSource", "points", "nodes",
        }

    def test_to_dict_leaves_out_collection_names_and_counts(self) -> None:
        report = assess_memory(
            {"records": LARGE_COLLECTION}, configured_limit_bytes=4 * GIB, host_ram_bytes=None
        )
        assert report.collections[0].name == "records"
        assert "records" not in repr(report.to_dict())


class TestQdrantServiceMemoryReport:
    """The provider gathers the inputs over raw REST and hands them to assess_memory."""

    @staticmethod
    def _service(responses: dict) -> tuple:
        pytest.importorskip("qdrant_client", reason="qdrant_client not installed")
        from app.services.vector_db.qdrant.qdrant import QdrantService

        async def request(*, type_: type, method: str, url: str, **kwargs: object) -> dict:
            outcome = responses[url]
            if isinstance(outcome, Exception):
                raise outcome
            return {"result": outcome}

        client = MagicMock()
        client.get_collections = AsyncMock(
            return_value=SimpleNamespace(
                collections=[SimpleNamespace(name=n.rsplit("/", 1)[1])
                             for n in responses if n.startswith("/collections/")]
            )
        )
        client.http.client.request = AsyncMock(side_effect=request)
        service = QdrantService(MagicMock())
        service.client = client
        return service, client

    @pytest.mark.asyncio
    async def test_uses_configured_limit_and_host_ram(self) -> None:
        service, client = self._service({
            "/collections/records": LARGE_COLLECTION,
            "/telemetry": {"app": {"system": {"ram_size": 64209252}}},
            "/cluster": {"status": "disabled"},
        })
        with patch.dict(os.environ, {"QDRANT_MEMORY_LIMIT": "4G"}):
            report = await service.memory_report()

        assert report.status == STATUS_INSUFFICIENT
        assert report.available_bytes == 4 * GIB
        assert report.nodes == 1
        telemetry_call = next(c for c in client.http.client.request.await_args_list
                              if c.kwargs["url"] == "/telemetry")
        assert telemetry_call.kwargs["params"] == {"details_level": 1}

    @pytest.mark.asyncio
    async def test_missing_telemetry_and_cluster_do_not_fail_the_report(self) -> None:
        service, _ = self._service({
            "/collections/records": LARGE_COLLECTION,
            "/telemetry": RuntimeError("telemetry disabled"),
            "/cluster": RuntimeError("no cluster"),
        })
        with patch.dict(os.environ, {"QDRANT_MEMORY_LIMIT": "12G"}):
            report = await service.memory_report()
        assert report.status == STATUS_OK
        assert report.nodes == 1

    @pytest.mark.asyncio
    async def test_counts_cluster_peers(self) -> None:
        info = _collection(5_000_000, 1536, shard_number=2, replication_factor=1)
        service, _ = self._service({
            "/collections/records": info,
            "/telemetry": {},
            "/cluster": {"status": "enabled", "peers": {"1": {}, "2": {}}},
        })
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QDRANT_MEMORY_LIMIT", None)
            report = await service.memory_report()
        assert report.nodes == 2
        assert report.status == STATUS_UNKNOWN

    @pytest.mark.asyncio
    async def test_unreadable_limit_is_ignored_with_a_warning(self) -> None:
        service, _ = self._service({
            "/collections/records": LARGE_COLLECTION,
            "/telemetry": {},
            "/cluster": {},
        })
        with patch.dict(os.environ, {"QDRANT_MEMORY_LIMIT": "plenty"}), \
             patch("app.services.vector_db.qdrant.qdrant.logger") as log:
            report = await service.memory_report()
        assert report.available_bytes is None
        assert report.status == STATUS_UNKNOWN
        log.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_collection_names_are_url_encoded(self) -> None:
        service, client = self._service({
            "/collections/a b": LARGE_COLLECTION,
            "/telemetry": {},
            "/cluster": {},
        })

        async def request(*, type_: type, method: str, url: str, **kwargs: object) -> dict:
            if url.startswith("/collections/"):
                assert url == "/collections/a%20b"
                return {"result": LARGE_COLLECTION}
            return {"result": {}}

        client.http.client.request = AsyncMock(side_effect=request)
        report = await service.memory_report()
        assert report.points == LARGE_COLLECTION["points_count"]

    @pytest.mark.asyncio
    async def test_other_providers_report_nothing(self) -> None:
        from app.services.vector_db.interface.vector_db import IVectorDBService

        class Minimal(IVectorDBService):
            pass

        for name in list(getattr(Minimal, "__abstractmethods__", ())):
            setattr(Minimal, name, lambda *_a, **_k: None)
        Minimal.__abstractmethods__ = frozenset()
        assert await Minimal().memory_report() is None
