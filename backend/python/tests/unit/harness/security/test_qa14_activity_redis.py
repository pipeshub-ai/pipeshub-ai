"""QA14-5 (SBX-30 on the real stream): the activity panel's frames, as they sit on Redis, carry no token.

UX5 redacts command output before it leaves the worker. Here ``run_code`` goes through the real assembler with the
activity bus on a real Redis (``REDIS_IT_URL``); the program prints every forbidden vector example (read from an
encoding, as an env dump or a config file would hold them) and the person's own text. The stream is read back raw
and decoded as Node's projector decodes it; the journaled record, its log and the call's result are scanned too.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
import uuid
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.livebus import (
    ACTIVITY_KEY_PREFIX,
    RedisLiveBus,
    decode_frame,
    stream_key,
)
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext
from harness_core.security.token_patterns import SECRET_MASK
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.canary import (
    Canaries,
    Surface,
    assert_no_canary,
    private_canary,
)
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.ids import SeededIds
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.support.sandbox_env import sandbox_backend, sandbox_env
from tests.support.token_vector import EXAMPLES
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("REDIS_IT_URL"), reason="needs a Redis (REDIS_IT_URL)"),
]

PRINTED = [example for _, example, entry in EXAMPLES if entry.forbidden]
PRIVATE = private_canary(SeededIds(145).uuid4)
CODE = "\n".join(
    [
        "import base64, sys",
        f"print({PRIVATE!r})",
        *(f"print('key: ' + base64.b64decode({base64.b64encode(e.encode()).decode()!r}).decode())" for e in PRINTED),
        f"print('err ' + base64.b64decode({base64.b64encode(PRINTED[0].encode()).decode()!r}).decode(), file=sys.stderr)",
        "",
    ]
)


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with ExitStack() as stack:
        stack.enter_context(w.deployment())
        stack.enter_context(
            patch("app.services.artifact_registry.ArtifactRegistryService", new=ParityArtifactRegistry(w.ORG, "ses-1"))
        )
        stack.enter_context(patch.dict(os.environ, {**sandbox_env("host"), "SANDBOX_ALLOW_NETWORK": "false"}))
        stack.enter_context(sandbox_backend("host"))
        yield


@pytest.fixture
async def redis() -> AsyncIterator[tuple[Any, str]]:
    from redis.asyncio import Redis

    client: Any = Redis.from_url(os.environ["REDIS_IT_URL"], decode_responses=True)  # pyright: ignore[reportUnknownMemberType]
    namespace = f"qa14-{uuid.uuid4().hex[:8]}"
    yield client, namespace
    async for key in client.scan_iter(f"{namespace}:*"):
        _ = await client.delete(key)
    await client.aclose()


async def test_qa14_5_frames_on_the_real_stream_the_record_and_the_result_carry_no_token(
    redis: tuple[Any, str],
) -> None:
    client, namespace = redis
    bus = RedisLiveBus(client, key_namespace=namespace).activity_bus()
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port), activity=bus)
    claim = w.claim(w.pinned_spec(tool_ids=[], sandbox="agent-code"))
    runtime: Any = await assembler.build(dataclasses.replace(claim))  # type: ignore[arg-type]
    ctx = ToolContext("run-1", w.ORG, "t1.0", "att-1", f"{w.ORG}:run-1:t1.0")
    execution = await runtime.tools.execute(
        ToolCall(id="c1", name="run_code", arguments={"code": CODE, "language": "python"}), ctx
    )
    await runtime.close()

    raw = await client.xrange(stream_key("run-1", namespace, ACTIVITY_KEY_PREFIX))
    frames = [decode_frame("run-1", dict(fields)) for _, fields in raw]
    shown = "".join(str(f.data.get("text", "")) for f in frames if f is not None and f.kind == "command_output")
    assert PRIVATE in shown and SECRET_MASK in shown, "the program ran and its output is on the stream, masked"
    [record] = [e for e in execution.events if e.type == "x.pipeshub.command.finished"]
    assert_no_canary(
        [
            Surface("Redis activity stream (raw fields)", [dict(fields) for _, fields in raw], owner_view=True),
            Surface("activity frames as Node decodes them", [json.loads(f["d"]) for _, f in raw], owner_view=True),
            Surface("journaled command record", record.data, owner_view=True),
            Surface("the call's result (journal and model)", execution, owner_view=True),
        ],
        Canaries(secrets=tuple(PRINTED), private=(PRIVATE,)),
    )
