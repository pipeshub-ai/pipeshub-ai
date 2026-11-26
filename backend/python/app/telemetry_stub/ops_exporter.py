from collections.abc import Callable, Mapping
from typing import Any


def exporter_from_env(
    governor: Any,  # noqa: ANN401
    service: str,
    identity: Callable[[], Mapping[str, Any]] | None = None,
) -> None:
    return None
