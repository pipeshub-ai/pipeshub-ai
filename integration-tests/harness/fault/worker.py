"""The fault suite's worker: ``app.harness_main.run_worker`` with a scripted runtime.

    python worker.py <run-dir>

Everything ``run_worker`` builds is the production composition: ``HarnessServices``
with the Redis KV configuration, the worker credential Node wrote, ``NodeJournal``,
``RedisLiveBus``, ``RunDriver``, ``RunClaimer`` and the ``MessagingFactory``
consumer on Kafka or Redis Streams. Injected: the assembler (a scripted model
and tools calling a ``FakeSaaS``), a driver that records what the checker needs,
and a short lease. Failpoints are armed from ``<run-dir>/failpoints.json`` and
scenario knobs come from ``<run-dir>/scenario.json``; neither is an env var.

Files the checker reads, each append fsynced so it survives SIGKILL:
``model-calls.jsonl`` (one line per model call), ``saas.jsonl`` (external
effects), ``dispositions.jsonl`` (each attempt's end, its kernel transcript and
an independent projection of the journal read back from Node).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from app.harness.adapters.content_store import PipesHubContentStore
from app.harness.presentation import (
    _error_answer,  # pyright: ignore[reportPrivateUsage]
)
from app.harness.worker.capabilities import WORKER_CAPABILITIES
from app.harness.worker.warmup import warm_up
from app.harness_main import run_worker
from harness_core import bindings
from harness_core.contracts.generated.common import ContentRef
from harness_core.driver import AssembledRuntime, RunDriver
from harness_core.effects.policy import WRITES_ASK, LayeredApprovalPolicy
from harness_core.failpoints import registry
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.messages import AssistantMessage, UserMessage
from harness_core.kernel.core.tool_content import tool_message_content
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.transcript import decode_assistant, decode_result
from harness_core.logs import HarnessLog
from harness_core.testing import ModelScript, Reply, ScriptedModel, ToolCallSpec, Turn
from harness_core.testing.durable import FunctionToolPort, ScriptedModelPort
from harness_core.testing.durable_ports import SilentRunPresenter
from harness_core.testing.fake_saas import FakeSaaS
from tests.unit.harness_core.durable.support import assert_paired, render_pairs

from dependencies import Clients, add_dependency_tools, model_port
from stack import HANDED_BACK
from latency import WaveDelegation, add_latency_tools, add_mcp_tools, chunk_texts, latency_script, paced

ROOT = Path(sys.argv[1])
SCENARIO: dict[str, Any] = json.loads((ROOT / "scenario.json").read_text())
LEASE_MS = int(SCENARIO["leaseMs"])
# Over the 16 KB inline limit, so the result goes through Node's sealed content route.
BIG_RESULT = "x" * 20_000

# Turn by turn: (answer text, tool calls).
SCRIPTS: dict[str, list[tuple[str, list[tuple[str, dict[str, Any]]]]]] = {
    "flow": [
        ("", [("lookup", {"q": "status"})]),
        ("", [("create_issue", {"title": "bug"})]),
        ("all done: SENTINEL-ANSWER", []),
    ],
    "nonidem": [
        ("", [("post_message", {"text": "SENTINEL-TOOLARG"})]),
        ("posted", []),
    ],
    "slow": [
        ("", [("slow_lookup", {"q": "status"})]),
        ("", [("create_issue", {"title": "after restart"})]),
        ("finished after node restart", []),
    ],
    # P1b T2: the user sends a message while the read runs; the run takes it after the read.
    "steer": [
        ("", [("steered_lookup", {"q": "leave policy"})]),
        ("noted your addition: SENTINEL-ANSWER", []),
    ],
    # One model call that answers: the latency rig's turn (LAT1).
    "answer": [
        ("SENTINEL-ANSWER", []),
    ],
    # The built-in assistant: it reads, then answers; it has no write tools.
    "chat": [
        ("", [("lookup", {"q": "refund policy"})]),
        ("Refunds are accepted within 30 days: SENTINEL-ANSWER", []),
    ],
}


PROJECTION_ATTEMPTS = 10


def _calls() -> list[dict[str, Any]]:
    path = ROOT / "model-calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def fsync_append(name: str, record: dict[str, Any]) -> None:
    with (ROOT / name).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
        f.flush()
        os.fsync(f.fileno())


def script_for(input_text: str) -> list[tuple[str, list[tuple[str, dict[str, Any]]]]]:
    """The admitted query's first word names the script; ``dep <tool>`` reads through one dependency (QA-17)."""
    words = input_text.split()
    rig = latency_script(words)
    if rig is not None:
        return rig
    if words[0] != "dep":
        return SCRIPTS[words[0]]
    tool = words[1]
    return [
        ("", [(tool, {"q": "status"})]),
        ("", [(tool, {"q": "again"})]),
        ("", [("create_issue", {"title": f"after {tool}"})]),
        (f"answered after {tool}", []),
    ]


def run_turn(messages: list[Any], input_text: str | None) -> int:
    """The call's turn within its run: assistant messages after the run's own input (a session's earlier runs come
    before it in the history, QA-22), or every assistant message when the input is not found."""
    start = 0
    if input_text is not None:
        for i, m in enumerate(messages):
            if isinstance(m, UserMessage) and m.content == input_text:
                start = i
    return 1 + sum(1 for m in messages[start:] if isinstance(m, AssistantMessage))


async def record_model_call(run_id: str, attempt_id: str, call: Any, input_text: str | None = None) -> tuple[int, bool]:
    """Logs the call (turn, user messages) and holds it at ``gateTurn`` until the checker opens the gate.

    Returns the call's turn and whether it is the run's first model call."""
    turn = run_turn(call.messages, input_text)
    users = sum(1 for m in call.messages if isinstance(m, UserMessage))
    first = not any(c["runId"] == run_id for c in _calls())
    fsync_append(
        "model-calls.jsonl",
        {"runId": run_id, "attemptId": attempt_id, "turn": turn, "users": users, "t": time.time()},
    )
    if SCENARIO.get("gateTurn") == turn:
        (ROOT / f"gate-{turn}.reached").write_text(run_id)
        while not (ROOT / f"gate-{turn}.open").exists():
            await asyncio.sleep(0.1)
    return turn, first


class RecordedModelPort:
    """The run's real model (QA-17), each call logged and gated as the scripted model's are."""

    def __init__(self, inner: Any, run_id: str, attempt_id: str) -> None:
        self._inner = inner
        self.run_id = run_id
        self.attempt_id = attempt_id

    def restore(self, raw: bytes, *, provider: str, transport: str) -> Any:
        return self._inner.restore(raw, provider=provider, transport=transport)

    async def sample(self, call: Any, on_delta: Any) -> Any:
        await record_model_call(self.run_id, self.attempt_id, call)
        return await self._inner.sample(call, on_delta)


class ScriptedModelCalls:
    """A ``DurableModelPort`` answering from the script by turn; each call is logged before it answers."""

    provider = "scripted"
    transport = "scripted"

    def __init__(self, run_id: str, attempt_id: str, input_text: str) -> None:
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.input_text = input_text
        self.script = script_for(input_text)

    def restore(self, raw: bytes, *, provider: str, transport: str) -> Any:
        if (provider, transport) != (self.provider, self.transport):
            return None
        return AssistantMessage.model_validate_json(raw)

    async def sample(self, call: Any, on_delta: Any) -> Any:
        turn, first = await record_model_call(self.run_id, self.attempt_id, call, self.input_text)
        # `firstCallDelayS`: only the run's first model call is slow (a call that outlasts a drain).
        delay = SCENARIO.get("firstCallDelayS") if first else None
        await asyncio.sleep(float(delay if delay is not None else SCENARIO.get("modelDelayS", 0)))
        text, calls = self.script[turn - 1]
        specs = [ToolCallSpec(id=f"call_t{turn}_{i}", name=name, args=args) for i, (name, args) in enumerate(calls)]
        chunks: list[str] | int | None = 3 if text else None
        if text.startswith("answer:"):
            chunks = chunk_texts(self.run_id, int(text.split(":")[1]))
            text = "".join(chunks)
        reply = Reply(text=text, tool_calls=specs, chunks=chunks)
        model = ScriptedModel(ModelScript(id=f"fault-{turn}", turns=[Turn(reply=reply)]))
        log = ROOT / "deltas.jsonl" if SCENARIO.get("deltaLog") else None
        interval = float(SCENARIO.get("tokenIntervalS", 0))
        return await ScriptedModelPort(model).sample(call, paced(on_delta, interval, log, self.run_id))


def scripted_tools(saas: FakeSaaS) -> FunctionToolPort:
    port = FunctionToolPort()

    async def lookup(args: Any, ctx: Any) -> ToolExecution:
        return ToolExecution(status="ok", content={"found": args.get("q"), "blob": BIG_RESULT})

    async def steered_lookup(args: Any, ctx: Any) -> ToolExecution:
        # Answers once the checker has queued its message, so the boundary after it always has input.
        (ROOT / "steer.ready").write_text(ctx.run_id)
        for _ in range(600):
            if (ROOT / "steer.sent").exists():
                break
            await asyncio.sleep(0.1)
        return ToolExecution(status="ok", content={"found": args.get("q")})

    async def slow_lookup(args: Any, ctx: Any) -> ToolExecution:
        (ROOT / "slow.started").write_text(ctx.run_id)
        await asyncio.sleep(float(SCENARIO.get("slowToolS", 0)))
        return ToolExecution(status="ok", content={"found": args.get("q")})

    async def create_issue(args: Any, ctx: Any) -> ToolExecution:
        effect = saas.call("create_issue", {**dict(args), "run": ctx.run_id}, idempotency_key=ctx.idempotency_key)
        return ToolExecution(status="ok", content={"issue": effect.receipt_id}, receipt={"id": effect.receipt_id})

    async def post_message(args: Any, ctx: Any) -> ToolExecution:
        payload = {**dict(args), "invocation": ctx.invocation_id, "run": ctx.run_id}
        effect = saas.call("post_message", payload, idempotency_key=None)
        return ToolExecution(status="ok", content={"posted": effect.receipt_id})

    port.add("lookup", lookup, tags=[("category", "read")])
    port.add("slow_lookup", slow_lookup, tags=[("category", "read")])
    port.add("steered_lookup", steered_lookup, tags=[("category", "read")])
    port.add("create_issue", create_issue, tags=[("type", "create"), ("idempotent", "true")])
    port.add("post_message", post_message, tags=[("category", "write")])
    return port


class _Parts:
    """The answer parts ``_error_answer`` replaces the final text of."""

    def __init__(self) -> None:
        self.parts: list[dict[str, Any]] = []

    def replace_final_text(self, text: str) -> None:
        self.parts = [{"type": "text", "content": text}]


class AnswerPresenter(SilentRunPresenter):
    """The answer the user's stream ends with, in ``PipesHubPresenter``'s shape (QA-17): the last turn's text, or
    the product's own words for a failure the model port classified. Without it every run ends ``RUN_ERROR``."""

    async def finalize(self, ending: Any) -> list[NewEvent]:
        if ending.outcome == "error":
            completion = _error_answer(_Parts(), ending.error_type)
            data: dict[str, Any] = {
                "outcome": "error", "answer": completion["answer"], "partTexts": [], "citations": [],
                "confidence": completion["confidence"], "answerMatchType": completion["answerMatchType"],
                "errorCode": completion["errorCode"],
            }
        else:
            text = ending.answer if ending.answer is not None else (ending.texts[-1] if ending.texts else "")
            data = {"outcome": ending.outcome, "answer": text, "partTexts": [text] if text else [], "citations": []}
        return [NewEvent("x.pipeshub.answer.finalized", data, critical=False)]


class ScriptedAssembler:
    def __init__(self) -> None:
        self.saas = FakeSaaS(log_path=ROOT / "saas.jsonl")
        self.clients = Clients(SCENARIO.get("dependencies") or {})

    def model(self, claim: Any) -> Any:
        if "model" in SCENARIO:
            return RecordedModelPort(model_port(SCENARIO["model"]), claim.run_id, claim.attempt_id)
        return ScriptedModelCalls(claim.run_id, claim.attempt_id, claim.input_text)

    async def build(self, claim: Any) -> AssembledRuntime:
        tools = scripted_tools(self.saas)
        add_dependency_tools(tools, self.clients)
        add_latency_tools(tools)
        if SCENARIO.get("mcpServers"):
            await add_mcp_tools(tools, list(SCENARIO["mcpServers"]))
        return AssembledRuntime(
            **({"delegation": WaveDelegation()} if claim.input_text.startswith("wave ") else {}),
            model=self.model(claim),
            tools=tools,
            **({"presenter": AnswerPresenter()} if SCENARIO.get("presentAnswer") else {}),
            content=PipesHubContentStore(claim.content),
            bindings=bindings.KernelBindings(),
            # `askWrites`: every write waits for a person, as an org's approval rules can make it (latency rig).
            **({"approval": LayeredApprovalPolicy((WRITES_ASK,))} if SCENARIO.get("askWrites") else {}),
        )


async def project_journal(journal: Any, claim: Any) -> list[list[str]]:
    """The transcript an independent reader derives from the journal, sealed content opened."""
    content = PipesHubContentStore(claim.content)

    async def blob(ref: Any) -> bytes:
        return await content.get(ContentRef.model_validate(ref))

    items: list[Any] = []
    turns: dict[Any, int] = {}
    queued: dict[tuple[Any, int], Any] = {}
    for event in await journal.read(claim.stream_id):
        data = event.data
        if event.type == "turn.started":
            turns[event.run_id] = int(str(data["turn"]))
        elif event.type == "input.queued":
            queued[(event.run_id, int(str(data["messageSeq"])))] = data["textRef"]
        elif event.type == "input.consumed":
            ref = queued.pop((event.run_id, int(str(data["messageSeq"]))))
            items.append(["user", (await blob(ref)).decode()])
        elif event.type == "message.completed" and data["role"] == "user":
            items.append(["user", (await blob(data["textRef"])).decode()])
        elif event.type == "model.response":
            message = decode_assistant(await blob(data["messageRef"]))
            calls = message.tool_calls or []
            items.append(["assistant", json.dumps({"text": message.text, "calls": [c.name for c in calls]})])
            items.append({"run": event.run_id, "turn": turns.get(event.run_id, 1), "calls": [c.id for c in calls], "results": {}})
        elif event.type == "tool.outcome":
            turn, index = (int(x) for x in str(data["invocationId"]).removeprefix("t").split("."))
            value, _ = decode_result(await blob(data["resultRef"]))
            for item in items:
                if isinstance(item, dict) and (item["run"], item["turn"]) == (event.run_id, turn):
                    item["results"].setdefault(item["calls"][index], str(tool_message_content(value)))
    out: list[list[str]] = []
    for item in items:
        if isinstance(item, dict):
            out.extend(["tool", f"{c}:{item['results'].get(c, 'cancelled')}"] for c in item["calls"])
        else:
            out.append(item)
    return out


def paired(messages: list[Any]) -> bool:
    try:
        assert_paired(messages)
    except AssertionError:
        return False
    return True


class RecordingDriver(RunDriver):
    """Records each attempt's end for the checker, after the production driver ran it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # `holdApprovals: false`: suspend at once on an approval, as the inline driver did before LAT1 (latency rig).
        if SCENARIO.get("holdApprovals") is False:
            kwargs["interactions"] = None
        super().__init__(*args, **kwargs)

    async def run_claimed(self, claim: Any, *args: Any, **kwargs: Any) -> Any:
        done = await super().run_claimed(claim, *args, **kwargs)
        # `dispositions: false` (latency rig): the checker's read of the whole session journal after each run
        # would load Node while the session's next run commits.
        if SCENARIO.get("dispositions") is False:
            return done
        transcript = list(getattr(done, "transcript", []) or [])
        record: dict[str, Any] = {
            "runId": claim.run_id,
            "attemptId": claim.attempt_id,
            "fence": claim.fence,
            "status": done.status,
            "kernel": [list(p) for p in render_pairs(transcript)],
            "paired": paired(transcript),
        }
        # An attempt that handed its run back is not the one the checker projects (it reads the last by fence),
        # and its token may already be superseded: reading with it failed ten times, a second apart, and held
        # the worker's drain for 10 s (FLK5, `f2-redis`).
        if done.status in HANDED_BACK:
            fsync_append("dispositions.jsonl", record)
            return done
        # Retried: during a rolling deploy the Node this reads through may be the one being replaced.
        for attempt in range(PROJECTION_ATTEMPTS):
            try:
                record["projection"] = await project_journal(self.journal, claim)
                record.pop("projectionError", None)
                break
            except Exception as exc:  # reported by the checker as a failed projection
                record["projectionError"] = f"{type(exc).__name__}: {exc}"
                if attempt + 1 < PROJECTION_ATTEMPTS:
                    await asyncio.sleep(1)
        fsync_append("dispositions.jsonl", record)
        return done


def dump_tasks() -> None:
    """SIGUSR1: every asyncio task's stack into the log, to see where a degraded run waits."""
    import io

    for task in asyncio.all_tasks():
        out = io.StringIO()
        task.print_stack(file=out)
        logging.getLogger("fault.worker").warning("task.stack %s\n%s", task.get_name(), out.getvalue())


async def _serve(options: dict[str, Any]) -> None:
    import signal

    asyncio.get_running_loop().add_signal_handler(signal.SIGUSR1, dump_tasks)
    if "model" in SCENARIO:
        # The run samples through the product's model stack, which production imports before it claims (C-17);
        # run_worker skips that for an injected assembler.
        await warm_up(HarnessLog.named("fault.worker"))
    await run_worker(
        assembler=ScriptedAssembler(),
        driver_type=RecordingDriver,
        lease_ms=LEASE_MS,
        renew_ms=LEASE_MS // 3,
        **options,
    )


def main() -> None:
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", stream=sys.stdout)
    for noisy in ("httpx", "httpcore", "aiokafka", "kafka", "urllib3", "asyncio", "redis"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    arms = ROOT / "failpoints.json"
    if arms.exists():
        registry.install_file(arms)
    options: dict[str, Any] = {}
    if "drainS" in SCENARIO:
        options["drain_s"] = float(SCENARIO["drainS"])
    if "capabilities" in SCENARIO:
        # Another release's advertisement (the mixed-fleet scenarios, RU-5).
        options["capabilities"] = WORKER_CAPABILITIES.model_copy(
            update={"fold_version": int(SCENARIO["capabilities"]["foldVersion"])}
        )
    asyncio.run(_serve(options))


if __name__ == "__main__":
    main()
