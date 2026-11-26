"""SR-7: the image generator's exception to the send-safe rule holds.

The provider call keeps no state and the artifact is written after it returns, so when the OpenAI SDK re-sends a
generation whose answer was lost, the cost is a second generation, never a second artifact. The tool is the
production ``ImageGenerator`` with the production OpenAI adapter, pointed at a loopback fake.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from app.agents.actions.image_generator import image_generator as module
from harness_core.effects.ambiguity import track_sends
from tests.unit.sources.client.saferetry_fakes import DROP, http_fake

if TYPE_CHECKING:
    from collections.abc import Coroutine

    import pytest

GENERATIONS = ("POST", "/v1/images/generations")


async def test_sr_7_a_regenerated_image_is_still_one_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    uploads: list[str] = []
    tasks: list[asyncio.Task[Any]] = []
    waits: list[float] = []

    async def upload(**kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        uploads.append(kwargs["file_name"])
        return {"recordId": "r1", "fileName": kwargs["file_name"]}

    async def no_wait(self: AsyncOpenAI, **kwargs: Any) -> None:  # noqa: ANN401 - the SDK's backoff seam
        waits.append(1.0)

    async def config(_: object) -> dict[str, object]:
        return {"provider": "openAI", "configuration": {"model": "gpt-image-1", "apiKey": "sk-qa"}}

    def register(_: str, task: Coroutine[Any, Any, Any] | asyncio.Task[Any]) -> None:
        tasks.append(task)  # type: ignore[arg-type]

    monkeypatch.setattr(module, "upload_bytes_artifact", upload)
    monkeypatch.setattr(module, "get_image_generation_config", config)
    monkeypatch.setattr(module, "register_task", register)
    monkeypatch.setattr(AsyncOpenAI, "_sleep_for_retry", no_wait)
    image = base64.b64encode(b"\x89PNG-qa").decode()
    with http_fake(created=1, data=[{"b64_json": image}]) as fake:
        fake.plan = [DROP]
        monkeypatch.setenv("OPENAI_BASE_URL", f"http://{fake.address}/v1")
        state = {"conversation_id": "c1", "org_id": "o1", "user_id": "u1", "config_service": object(), "blob_store": object()}
        tool = module.ImageGenerator(state)  # type: ignore[arg-type]
        with track_sends() as sends:
            ok, payload = await tool.generate_image(prompt="a lighthouse", file_name="lighthouse")
        await asyncio.gather(*tasks)
    assert ok, payload
    assert json.loads(payload)["count"] == 1
    assert fake.writes() == [GENERATIONS, GENERATIONS] and waits == [1.0]
    assert len(uploads) == 1
    assert not sends.ambiguous
