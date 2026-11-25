"""WP-61 (C6.12): a run past its egress cap ends with ``egress_volume_exceeded``.

The egress proxy marks a capped run's Model Gateway requests (``X-Egress-Capped: volume``, never a sandbox's own).
The gateway refuses each such call after the run's token is checked, in the caller's own wire, with the reason in
the error's code and tagged in its message (``[gateway:egress_volume_exceeded]``), which every engine's mapper reads.

R11D-EVX-1 every wire (Chat Completions, Responses, Messages, Gemini) refuses a marked call 403 with the reason in
           its message and refusal header, and the provider is never asked.
R11D-EVX-2 the refusal is reported on the run's journal (``gateway.refused``) once.
R11D-EVX-3 an unmarked call of the same run is served; an unauthenticated marked call is still a 401 first.
"""

from __future__ import annotations

import pytest

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.gateway.errors import EgressVolumeExceeded
from harness_core.gateway.model_gateway import EGRESS_CAPPED_HEADER as GATEWAY_MARK
from harness_core.gateway.model_server import REFUSAL_HEADER
from harness_core.gateway.refusal_reports import RefusalReport
from harness_core.sandbox.egress.policy import (
    EGRESS_CAPPED_HEADER,
    EGRESS_CAPPED_VOLUME,
)
from tests.support.loopback_client import loopback_client
from tests.unit.harness_core.gateway.test_chat_wire import OPENCODE_BODY
from tests.unit.harness_core.gateway.test_model_server import (  # noqa: F401, TC001 - the fixture and its type
    Served,
    served,
)

CODE = "egress_volume_exceeded"
TAG = f"[gateway:{CODE}]"
MARK = {EGRESS_CAPPED_HEADER: EGRESS_CAPPED_VOLUME}

WIRES = [
    ("/v1/chat/completions", OPENCODE_BODY),
    ("/v1/responses", {"model": ENGINE_MODEL_ID, "input": "go"}),
    ("/v1/messages", {"model": ENGINE_MODEL_ID, "max_tokens": 16, "messages": [{"role": "user", "content": "go"}]}),
    (f"/v1beta/models/{ENGINE_MODEL_ID}:generateContent", {"contents": [{"role": "user", "parts": [{"text": "go"}]}]}),
]


def test_r11d_evx_the_refusal_is_a_403_with_its_own_code_and_the_proxy_and_gateway_agree_on_the_mark() -> None:
    refused = EgressVolumeExceeded()
    assert (refused.reason_code, refused.http_status) == (CODE, 403)
    assert GATEWAY_MARK == EGRESS_CAPPED_HEADER.lower()


@pytest.mark.parametrize(("path", "body"), WIRES)
async def test_r11d_evx_1_every_wire_refuses_a_marked_call_with_the_reason(
    served: Served,  # noqa: F811
    path: str,
    body: dict[str, object],
) -> None:
    async with loopback_client() as client:
        response = await client.post(
            f"{served.url}{path}", json=body, headers={"Authorization": f"Bearer {served.token}", **MARK}
        )
    assert response.status_code == 403
    assert response.headers[REFUSAL_HEADER] == CODE
    assert TAG in response.text
    assert served.rig.transport.calls == []


async def test_r11d_evx_2_the_refusal_is_reported_on_the_runs_journal_once(served: Served) -> None:  # noqa: F811
    async with loopback_client() as client:
        for _ in range(2):
            response = await client.post(
                f"{served.url}/v1/chat/completions",
                json=OPENCODE_BODY,
                headers={"Authorization": f"Bearer {served.token}", **MARK},
            )
            assert response.status_code == 403
    await served.rig.refusals.drain()
    reports = [r.report for r in served.rig.refusal_sink.records]  # type: ignore[attr-defined]
    assert reports == [RefusalReport("model", CODE, 403)]


async def test_r11d_evx_3_an_unmarked_call_is_served_and_a_marked_one_without_a_token_is_401(
    served: Served,  # noqa: F811
) -> None:
    async with loopback_client() as client:
        anonymous = await client.post(f"{served.url}/v1/chat/completions", json=OPENCODE_BODY, headers=MARK)
        fine = await client.post(
            f"{served.url}/v1/chat/completions", json=OPENCODE_BODY, headers={"Authorization": f"Bearer {served.token}"}
        )
    assert anonymous.status_code == 401
    assert fine.status_code == 200
