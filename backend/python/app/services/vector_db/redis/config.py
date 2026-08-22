from dataclasses import dataclass

# Supported dense-vector storage dtypes for the RediSearch VECTOR field.
# FLOAT16 halves index memory vs FLOAT32 with negligible recall loss and, unlike
# scalar quantization, needs no calibration/scale state — it is a pure lossy cast
# applied identically to documents and queries.
_VALID_DENSE_DTYPES = ("FLOAT32", "FLOAT16")
_DEFAULT_DENSE_DTYPE = "FLOAT16"


@dataclass
class RedisVectorConfig:
    host: str = "localhost"
    port: int = 6379
    password: str | None = None
    db: int = 0
    timeout: int = 300
    # Dense vector storage type for newly created indexes. Existing indexes keep
    # whatever dtype they were created with (query encoding is resolved from the
    # live index, not this setting).
    dense_dtype: str = _DEFAULT_DENSE_DTYPE

    @property
    def redis_config(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "password": self.password,
            "db": self.db,
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RedisVectorConfig":
        password = data.get("password") or None
        if isinstance(password, str) and not password.strip():
            password = None
        dense_dtype = str(data.get("dense_dtype") or _DEFAULT_DENSE_DTYPE).upper()
        if dense_dtype not in _VALID_DENSE_DTYPES:
            raise ValueError(
                f"Invalid Redis dense_dtype {dense_dtype!r}; "
                f"expected one of {_VALID_DENSE_DTYPES}"
            )
        return cls(
            host=data.get("host", "localhost"),
            port=int(data.get("port", 6379)),
            password=password,
            db=int(data.get("db", 0)),
            timeout=int(data.get("timeout", 300)),
            dense_dtype=dense_dtype,
        )
