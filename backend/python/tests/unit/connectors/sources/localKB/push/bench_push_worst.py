"""Measure task 1 (validate_body) on one body in a fresh process, from ONE baseline that includes the
structural pass. Not collected by pytest. Usage:

    nice -n 19 python -I tests/unit/connectors/sources/localKB/push/bench_push_worst.py <body.json> [schema|none]

Prints one line: body size, pass time, total CPU, peak RSS growth over the baseline, outcome.
"""
import gc
import os
import sys
import time

sys.path.insert(0, os.getcwd())


def status(key: str) -> int:
    with open("/proc/self/status") as f:
        return next(int(line.split()[1]) * 1024 for line in f if line.startswith(key))


def main() -> None:
    from app.connectors.sources.localKB.push import structure, worker
    from app.connectors.sources.localKB.push.contract import PushRefusal
    from app.models.record_metadata import (
        MetadataSchema,
        PropertyDefinition,
        PropertyType,
    )

    path = sys.argv[1]
    body = open(path, "rb").read()
    schema = MetadataSchema(
        schema_id="o:k:1",
        properties=tuple(PropertyDefinition(name=f"p{i:03d}", type=PropertyType.STRING_LIST, display_label=f"P{i}") for i in range(256)),
    ).model_dump(mode="json")
    timing: dict[str, float] = {}
    real_pass = structure.structure_pass

    def timed(b: bytes, names: frozenset) -> dict:
        t = time.process_time()
        try:
            return real_pass(b, names)
        finally:
            timing["pass"] = time.process_time() - t

    structure.structure_pass = timed
    gc.collect()
    with open("/proc/self/clear_refs", "w") as f:
        f.write("5")
    base = status("VmRSS")
    cpu = time.process_time()
    try:
        v = worker.validate_body(body, schema_id="o:k:1", schema=schema, out_path=path + ".out")
        outcome = f"ACCEPTED blocks={v.block_count} warnings={v.warnings} stored={v.size}"
    except PushRefusal as r:
        outcome = f"{r.status} {r.reason} {r.location}"
    cpu = time.process_time() - cpu
    peak = status("VmHWM") - base
    sys.stdout.write(
        f"{os.path.basename(path):18s} body={len(body)/1e6:6.2f}MB pass={timing.get('pass', 0):6.2f}s "
        f"cpu={cpu:6.2f}s peak=+{peak/2**20:7.1f}MiB -> {outcome}\n"
    )
    if os.path.exists(path + ".out"):
        os.unlink(path + ".out")


main()
