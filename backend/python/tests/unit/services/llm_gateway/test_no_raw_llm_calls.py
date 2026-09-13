"""Every indexing-time LLM call goes through the gateway (cap, breaker, timeout, typed outages)."""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[4] / "app"
INDEXING_PATHS = [APP / "modules" / "parsers", APP / "modules" / "transformers", APP / "modules" / "extraction",
                  APP / "utils" / "table_enrichment.py"]


def test_no_indexing_code_calls_a_model_directly() -> None:
    offenders = []
    for root in INDEXING_PATHS:
        for path in [root] if root.is_file() else sorted(root.rglob("*.py")):
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                if re.search(r"\.ainvoke\(", line) and not line.lstrip().startswith("#"):
                    offenders.append(f"{path.relative_to(APP)}:{number}: {line.strip()}")
    assert not offenders, "call models through get_llm_gateway().invoke:\n" + "\n".join(offenders)
