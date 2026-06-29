from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OpenSearchConfig:
    host: str = "localhost"
    port: int = 9200
    username: str = "admin"
    password: str = "admin"
    use_ssl: bool = False
    verify_certs: bool = False
    ssl_show_warn: bool = False
    timeout: int = 300
    # Pluggable auth seam.  Only "basic" is implemented.  Adding AWS SigV4 later
    # is a single new branch in _build_client() — zero call-site changes.
    # Any unrecognised value raises ValueError at connect() time.
    auth_type: str = "basic"

    # -------------------------------------------------------------------------
    # HNSW index tuning
    # -------------------------------------------------------------------------
    # m: number of bidirectional links per node. Lower = faster insert + search,
    # lower memory. 16 is the standard recommendation; Qdrant also defaults to 16.
    # Setting 48 (the old default) tripled memory usage and slowed inserts O(m*log n).
    m: int = 16
    # ef_construction: candidate list size during graph build. Higher = better
    # graph quality but slower indexing. 128 is the OpenSearch/Lucene recommended
    # default and halves the beam-search cost vs 256 with negligible recall loss.
    ef_construction: int = 128
    # ef_search: candidate list size during k-NN search (Lucene engine).
    # Without this, OpenSearch 3.x defaults ef_search to k which hurts recall.
    ef_search: int = 100

    # -------------------------------------------------------------------------
    # Quantization
    # -------------------------------------------------------------------------
    # quantization_bits: Lucene sq encoder bit depth.
    #   7  → 7-bit scalar quantization (~4x memory reduction, ~INT8 equivalent)
    #   0  → disabled (full FP32, no encoder block in mapping)
    quantization_bits: int = 7
    # confidence_interval: fraction of vectors used to compute quantile bounds
    # for the scalar quantizer. 0.99 clips 1% of outliers; reduces recall risk.
    confidence_interval: float = 0.99

    # -------------------------------------------------------------------------
    # Hybrid search / RRF pipeline
    # -------------------------------------------------------------------------
    # rank_constant for the RRF score-ranker-processor.
    # Higher values reduce the impact of high-ranked documents; 60 is the
    # OpenSearch default and a well-established empirical starting point.
    rrf_rank_constant: int = 60

    @property
    def opensearch_config(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "useSsl": self.use_ssl,
            "verifyCerts": self.verify_certs,
            "sslShowWarn": self.ssl_show_warn,
            "timeout": self.timeout,
            "authType": self.auth_type,
            "m": self.m,
            "efConstruction": self.ef_construction,
            "efSearch": self.ef_search,
            "quantizationBits": self.quantization_bits,
            "confidenceInterval": self.confidence_interval,
            "rrfRankConstant": self.rrf_rank_constant,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OpenSearchConfig":
        return cls(
            host=data.get("host", "localhost"),
            port=int(data.get("port", 9200)),
            username=data.get("username", "admin"),
            password=data.get("password", "admin"),
            use_ssl=bool(data.get("useSsl", data.get("use_ssl", False))),
            verify_certs=bool(data.get("verifyCerts", data.get("verify_certs", False))),
            ssl_show_warn=bool(data.get("sslShowWarn", data.get("ssl_show_warn", False))),
            timeout=int(data.get("timeout", 300)),
            auth_type=data.get("authType", data.get("auth_type", "basic")),
            m=int(data.get("m", 16)),
            ef_construction=int(data.get("efConstruction", data.get("ef_construction", 256))),
            ef_search=int(data.get("efSearch", data.get("ef_search", 100))),
            quantization_bits=int(data.get("quantizationBits", data.get("quantization_bits", 7))),
            confidence_interval=float(data.get("confidenceInterval", data.get("confidence_interval", 0.99))),
            rrf_rank_constant=int(data.get("rrfRankConstant", data.get("rrf_rank_constant", 60))),
        )
