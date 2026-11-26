"""OPS1-4-13 (CPD-6 §10.3 F-A) on real processes, per broker: deploys leave no consumer groups behind.

Every Node and every worker follows ``control-plane-events`` and must see every event. On Redis Streams a
subscriber holds no consumer group, so however a process ends (SIGTERM, SIGKILL) the stream has none afterwards;
Redis never expires one. On Kafka each process is a group of its own, which the broker removes once it is empty and
its offsets expire: what the suite can hold it to is that a dead process's group is empty, and that there is at most
one per process started. Afterwards a live worker still holds its layer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import stack

pytestmark = pytest.mark.fault

TOPIC = "control-plane-events"
NODE_CYCLES = 20
WORKER_CYCLES = 6
# kafkajs gives a member 30 s without a heartbeat before the broker drops it; aiokafka, 10 s.
KAFKA_SESSION_S = 30


def _live_layer_holder(fault_stack: stack.FaultStack, out_dir: Path, name: str) -> tuple[stack.Process, Path]:
    """A worker's live layer (``live_probe.py``): the production subscriber of the topic, in a process of its own."""
    out = out_dir / f"{name}.jsonl"
    argv = [sys.executable, str(stack.HERE / "live_probe.py"), str(out), stack.ORG]
    return stack.Process(name, argv, stack.PYTHON_ROOT, fault_stack.env, out_dir / f"{name}.log", "LIVE PROBE READY"), out


def test_ops1_4_13_deploys_leave_no_consumer_groups_and_a_live_worker_keeps_its_layer(
    fault_stack: stack.FaultStack, credential: str, tmp_path: Path
) -> None:
    node = stack.node_process(fault_stack.env, fault_stack.out / "node-groups.log", port=stack.extra_port(3))
    # Kafka keeps the groups of every process the session started before this case; only this case's are counted.
    before = set(stack.kafka_groups(TOPIC)) if fault_stack.broker == "kafka" else set()
    started = 0
    try:
        for cycle in range(NODE_CYCLES):
            node.start()
            started += 1
            if cycle % 2 == 0:
                assert node.term(30) is not None, "the Node did not stop on SIGTERM"
            else:
                node.kill9()
        for cycle in range(WORKER_CYCLES):
            holder, _ = _live_layer_holder(fault_stack, tmp_path, f"holder-{cycle}")
            holder.start()
            started += 1
            if cycle % 2 == 0:
                holder.term(30)
            holder.kill9()
    finally:
        node.kill9()

    live, seen_file = _live_layer_holder(fault_stack, tmp_path, "live")
    live.start()
    started += 1
    try:
        if fault_stack.broker == "redis":
            assert stack.stream_groups(TOPIC) == []
        else:
            # The session's Node and the live holder; every other group's process is gone.
            stack.wait_for(lambda: len(stack.kafka_groups(TOPIC, members=True)) <= 2, KAFKA_SESSION_S + 30, "dead groups emptied")
            assert len(set(stack.kafka_groups(TOPIC)) - before) <= started
        assert stack.jsonl(seen_file), "the live holder reads the deployment's layer"
    finally:
        live.kill9()
