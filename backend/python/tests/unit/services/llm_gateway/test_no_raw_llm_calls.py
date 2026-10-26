"""Every indexing-time LLM call goes through the gateway (cap, breaker, timeout, typed outages)."""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[4] / "app"
INDEXING_PATHS = [APP / "modules" / "parsers", APP / "modules" / "transformers", APP / "modules" / "extraction",
                  APP / "modules" / "named_entities", APP / "modules" / "entity_resolution",
                  APP / "utils" / "table_enrichment.py"]
# Query-time: entity linking verifies the mentions in a person's question, not a record.
QUERY_TIME_PATHS = [APP / "modules" / "named_entities" / "linking"]


def test_every_scanned_path_exists() -> None:
    # A renamed or moved path would otherwise be scanned as empty and pass.
    missing = [str(path.relative_to(APP)) for path in INDEXING_PATHS + QUERY_TIME_PATHS if not path.exists()]
    assert not missing, f"update the scanned paths: {missing}"


def test_no_indexing_code_calls_a_model_directly() -> None:
    offenders = []
    for root in INDEXING_PATHS:
        for path in [root] if root.is_file() else sorted(root.rglob("*.py")):
            if any(path.is_relative_to(excluded) for excluded in QUERY_TIME_PATHS):
                continue
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                if re.search(r"\.ainvoke\(", line) and not line.lstrip().startswith("#"):
                    offenders.append(f"{path.relative_to(APP)}:{number}: {line.strip()}")
    assert not offenders, "call models through get_llm_gateway().invoke:\n" + "\n".join(offenders)
