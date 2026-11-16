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


# ---- the wire hooks reach every model ----------------------------------------------------------------------

import ast  # noqa: E402
import tomllib  # noqa: E402

PYPROJECT = Path(__file__).resolve().parents[4] / "pyproject.toml"


def _function(source: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined")


def test_models_are_instrumented_and_timeouts_are_floats() -> None:
    """Every chat or embeddings model a factory returns passes through ``instrument_model`` (which installs the wire
    hooks), no model is built with an ``httpx.Timeout`` (three wrappers reject it), and the SDKs whose private clients
    the hooks reach have upper bounds."""
    aimodels = (APP / "utils" / "aimodels.py").read_text()
    for factory, decorator in [
        ("get_generator_model", "_decorate_chat_model"),
        ("get_embedding_model", "_decorate_embeddings"),
        ("get_default_embedding_model", "_decorate_embeddings"),
    ]:
        returns = [n for n in ast.walk(_function(aimodels, factory)) if isinstance(n, ast.Return)]
        assert returns and all(
            isinstance(r.value, ast.Call) and getattr(r.value.func, "id", "") == decorator for r in returns
        ), f"{factory} must return through {decorator}"
    for decorator in ("_decorate_chat_model", "_decorate_embeddings"):
        calls = {getattr(n.func, "id", "") for n in ast.walk(_function(aimodels, decorator)) if isinstance(n, ast.Call)}
        assert "instrument_model" in calls, f"{decorator} must call instrument_model"

    offenders = []
    for path in sorted(APP.rglob("*.py")):
        text = path.read_text()
        if "httpx.Timeout(" not in text or path.name == "wire.py":
            continue
        if path.is_relative_to(APP / "utils") and path.name in {"aimodels.py", "llm.py"}:
            offenders.append(str(path.relative_to(APP)))
    assert not offenders, f"pass a float timeout to a model, never httpx.Timeout: {offenders}"

    deps = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    for package in ("google-genai", "anthropic"):
        spec = next((d for d in deps if d.split("<")[0].split(">")[0].split("=")[0].strip() == package), "")
        assert "<" in spec, f"{package} needs an upper bound (found {spec!r})"
