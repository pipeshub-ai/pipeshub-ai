"""``LiveLayerSource`` over Node's ``GET /internal/control-plane/orgs/{orgId}/live`` (worker credential).

One attempt per load: the cache's time to live is the retry, and a layer that cannot be read is never guessed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol
from urllib.parse import quote

from pydantic import ValidationError

from app.control_plane.contracts.generated.live_layer import (
    LiveLayer as LiveLayerDocument,
)
from app.control_plane.runtime.live_layer import LiveLayerUnavailable
from app.harness.journal_node.client import INTERNAL_PREFIX
from app.harness.journal_node.http import NodeServiceClient
from harness_core.policy.retired import without_retired_keys

if TYPE_CHECKING:
    import httpx

OK = 200
UNCHANGED = 204


class LiveCredentialSource(Protocol):
    async def worker_credential(self) -> str: ...


class NodeLiveLayerSource:
    def __init__(
        self,
        *,
        base_url: str,
        credentials: LiveCredentialSource,
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self._credentials = credentials
        self._node = NodeServiceClient(
            base_url=base_url, timeout_s=timeout_s, http_client=http_client, service_name="node.live_layer"
        )

    async def aclose(self) -> None:
        await self._node.aclose()

    async def load(self, org_id: str, since: int | None) -> LiveLayerDocument | None:
        try:
            bearer = await self._credentials.worker_credential()
        except Exception as exc:
            raise LiveLayerUnavailable(f"no worker credential: {type(exc).__name__}") from None
        response = await self._node.send(
            "GET",
            f"{INTERNAL_PREFIX}/control-plane/orgs/{quote(org_id, safe='')}/live",
            bearer=bearer,
            params=None if since is None else {"since": since},
            unreachable=lambda exc: LiveLayerUnavailable(f"Node unreachable: {type(exc).__name__}"),
        )
        if response.status_code == UNCHANGED and since is not None:
            return None
        if response.status_code != OK:
            raise LiveLayerUnavailable(f"Node answered {response.status_code}")
        try:
            document = LiveLayerDocument.model_validate(_without_retired_keys(json.loads(response.content)))
        except (ValidationError, ValueError) as invalid:
            # The count only: a validation message quotes the value it refuses.
            count = invalid.error_count() if isinstance(invalid, ValidationError) else 1
            raise LiveLayerUnavailable(f"Node answered a live layer with {count} invalid members") from None
        if document.org_id.root != org_id:
            raise LiveLayerUnavailable("Node answered another org's live layer")
        return document


def _without_retired_keys(document: object) -> object:
    """An older Node may still send a retired rollout key (CTL-5): it is stripped, never a reason to refuse the layer."""
    if not isinstance(document, dict) or not isinstance(document.get("layers"), list):
        return document
    layers = [
        {**scoped, "layer": without_retired_keys(scoped["layer"])} if isinstance(scoped, dict) and "layer" in scoped else scoped
        for scoped in document["layers"]
    ]
    return {**document, "layers": layers}


__all__ = ["LiveCredentialSource", "NodeLiveLayerSource"]
