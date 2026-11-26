"""CTL-5: a policy layer stored before CTL-5 may carry a retired rollout key. Read, the key is stripped and every
other dimension applies; the live layer is never refused for it. Shared cases: contracts/harness/vectors/retired-layer-keys.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness_core.policy.retired import RETIRED_ROLLOUT_KEYS, read_policy_layer

VECTOR = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts/harness/vectors/retired-layer-keys.json").read_text(encoding="utf-8")
)


def test_the_reader_strips_exactly_the_vectors_keys() -> None:
    assert sorted(RETIRED_ROLLOUT_KEYS) == sorted(VECTOR["retiredRolloutKeys"])


@pytest.mark.parametrize("case", VECTOR["cases"], ids=[c["id"] for c in VECTOR["cases"]])
def test_read_as_the_vector_says(case: dict[str, Any]) -> None:
    assert read_policy_layer(case["stored"]) == case["read"]


async def test_the_live_layer_from_node_keeps_a_deny_beside_a_retired_key() -> None:
    import httpx

    from app.harness.journal_node.live_layer import NodeLiveLayerSource

    stored = VECTOR["cases"][0]["stored"]
    live = {**stored, "scope": {"kind": "live"}}
    body = {"schemaVersion": 1, "orgId": "org-1", "revision": 3, "layers": [{"layer": live}], "revokedBindings": []}

    class Credentials:
        async def worker_credential(self) -> str:
            return "token"

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    source = NodeLiveLayerSource(
        base_url="http://node.test", credentials=Credentials(), http_client=httpx.AsyncClient(transport=transport, base_url="http://node.test")
    )
    document = await source.load("org-1", None)
    assert document is not None
    (layer,) = document.layers
    dumped = layer.layer.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert dumped["approvals"] == {"tools": {"jira@inst-42/create_issue": "deny"}}
    assert dumped["rollout"] == {"ENABLE_SKILLS": False}
