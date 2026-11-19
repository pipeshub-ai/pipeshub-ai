"""The same request answers byte for byte the same in separate processes (string hashing differs per process).
"""
import subprocess
import sys

SCRIPT = r"""
import hashlib, json, random, sys
from tests.unit.modules.graph_view.test_paging import random_spec
from tests.unit.modules.graph_view.world import World, expand, revalidate
h = hashlib.sha256()
for seed in range(40):
    rng = random.Random(seed)
    spec, anchors = random_spec(rng)
    for limit in (1, 2, 100):
        cursor = None
        for _ in range(30):
            res = expand(World(spec), "u", anchors, limit=limit, includeTests=bool(seed % 2), **({"cursor": cursor} if cursor else {}))
            h.update(res.content)
            cursor = res.json().get("page", {}).get("nextCursor") if res.status_code == 200 else None
            if not cursor:
                break
    ids = [f"block:{b}" for b in spec.blocks]
    h.update(revalidate(World(spec), "u", ids).content)
print(h.hexdigest())
"""


def test_answers_do_not_depend_on_the_process() -> None:
    digests = set()
    for seed in ("0", "1", "12345"):
        out = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, check=True,
                             env={**__import__("os").environ, "PYTHONHASHSEED": seed})
        digests.add(out.stdout.strip().splitlines()[-1])
    assert len(digests) == 1, digests
