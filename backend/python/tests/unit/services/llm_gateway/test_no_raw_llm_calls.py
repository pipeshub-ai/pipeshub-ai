"""Every indexing-time LLM call goes through the gateway (cap, breaker, timeout, typed outages)."""

import re
from pathlib import Path

import pytest

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


# ---- one retry layer: no raw paid call, and no SDK retries on an indexing factory path ----------------------------

# Every module that makes paid calls while indexing a record: parsers, transformers (vectorstore included), extraction,
# named entities, entity resolution, the table enrichment helper, the multimodal embedding providers and the pipeline.
PAID_CALL_PATHS = [
    *INDEXING_PATHS,
    APP / "services" / "embeddings" / "multimodal",
    APP / "utils" / "indexing_helpers.py",
    APP / "events" / "processor.py",
    APP / "modules" / "pipeline",
]
_RAW_CALL = re.compile(
    r"^(ainvoke|invoke|astream|astream_events|astream_log|stream|agenerate|generate|abatch|batch"
    r"|a?embed_(query|documents|images?|content|texts?|batch))$"
)
# The names a gateway goes by in the paid modules.
_GATEWAYS = frozenset({
    "get_llm_gateway()", "get_embedding_gateway()", "gateway", "llm_gateway", "embedding_gateway", "self._gateway",
    "self.gateway", "self._llm_gateway", "self._embedding_gateway",
})

# (file relative to app/, enclosing function) -> why a raw call is allowed there. A listed function that stops existing
# fails the test, so the list cannot rot into permission for something else.
RAW_CALL_ALLOWANCES = {
    ("modules/transformers/vectorstore.py", "_compute_sparse_embeddings"): "local BM25 vectors: no provider, no cost",
    ("modules/transformers/entity_vectorstore.py", "_embed_sparse"): "local BM25 vectors: no provider, no cost",
    ("modules/transformers/entity_vectorstore.py", "_query_vectors"): "query-time sparse vectors (local BM25) for a search",
    ("modules/transformers/vectorstore.py", "_build_embedding_model"):
        "the local embedding server's size probe: not paid, its client retries; a remote model goes through probe_dimension",
    ("modules/transformers/entity_vectorstore.py", "_embedding_dimension"):
        "the local embedding server's size probe: not paid, its client retries; a remote model goes through probe_dimension",
    ("modules/transformers/entity_vectorstore.py", "_embed"):
        "the local embedding server: not paid, its client retries; a remote model goes through EmbeddingGateway.call",
    ("modules/transformers/vectorstore.py", "_embed_documents_with_retry"):
        "the per-attempt body of the loop whose retry decision is EmbeddingGateway.decide (asserted below)",
    ("modules/transformers/vectorstore.py", "_process_image_embeddings"):
        "multimodal providers: each paid request inside them goes through EmbeddingGateway.call (asserted below)",
    ("modules/parsers/pdf/opencv_layout_analyzer.py", "_rasterize_page"): "a PDF byte stream, not a model",
    ("modules/named_entities/strategies/agent/transport.py", "_stream"):
        "an agent turn streamed under the indexing slot; the agent's retries are off (transports)",
}


def _is_gateway(receiver: ast.AST) -> bool:
    """Whether ``receiver`` is, by name, a gateway (``get_llm_gateway()``, ``gateway``, ``self._gateway`` ...), and not an
    object that merely has one in its name (``gateway.model``)."""
    return ast.unparse(receiver) in _GATEWAYS


def _raw_calls(path: Path) -> list[tuple[int, str, str]]:
    """(line, attribute, enclosing function) of every use of a model or embedder method that is not a gateway's: called
    (``model.ainvoke(x)``), handed over (``asyncio.to_thread(embeddings.embed_documents, x)``, ``functools.partial``),
    aliased (``call = model.ainvoke``) or looked up by name (``getattr(model, "ainvoke")``). Inside a gateway call only a
    method reference passed to it, or a call in a deferred ``lambda``, is the gateway's own; an eagerly evaluated argument
    (``gateway.invoke(llm, llm.invoke(m))``) runs before the gateway starts."""
    tree = ast.parse(path.read_bytes())
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and _RAW_CALL.match(node.attr):
            attribute, receiver = node.attr, node.value
        elif (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
            and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
            and _RAW_CALL.match(node.args[1].value)
        ):
            attribute, receiver = node.args[1].value, node.args[0]
        else:
            continue
        if _is_gateway(receiver):
            continue
        child: ast.AST = node
        ancestor = parents.get(node)
        crossed_deferred, steps, exempt = False, 1, False
        while ancestor is not None:
            crossed_deferred = crossed_deferred or isinstance(child, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef))
            if isinstance(ancestor, ast.Call) and isinstance(ancestor.func, ast.Attribute) and _is_gateway(ancestor.func.value):
                # A method reference passed straight to the gateway, or anything inside a callback it will call.
                exempt = crossed_deferred or (steps == 1 and child is node)
                break
            child, ancestor, steps = ancestor, parents.get(ancestor), steps + 1
        if exempt:
            continue
        scope: ast.AST | None = node
        while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = parents.get(scope)
        found.append((node.lineno, attribute, scope.name if scope is not None else "<module>"))  # type: ignore[union-attr]
    return found


def test_no_raw_paid_calls() -> None:
    """No ``.ainvoke/.invoke/.astream/.agenerate/.aembed_*/.embed_*`` outside a gateway in the paid indexing modules. A
    raw call has no retry budget, breaker, cap or Retry-After handling, so it would add a second retry layer or none."""
    offenders = []
    used: set[tuple[str, str]] = set()
    for root in PAID_CALL_PATHS:
        for path in [root] if root.is_file() else sorted(root.rglob("*.py")):
            if any(path.is_relative_to(excluded) for excluded in QUERY_TIME_PATHS):
                continue
            relative = path.relative_to(APP).as_posix()
            for line, attribute, function in _raw_calls(path):
                if (relative, function) in RAW_CALL_ALLOWANCES:
                    used.add((relative, function))
                    continue
                offenders.append(f"{relative}:{line}: .{attribute}() in {function}")
    assert not offenders, "call models through the gateways (get_llm_gateway().invoke, get_embedding_gateway().call):\n" + "\n".join(offenders)
    unused = set(RAW_CALL_ALLOWANCES) - used
    assert not unused, f"these allowances no longer match a raw call; remove them: {sorted(unused)}"


def test_the_allowed_embedding_paths_still_use_the_gateway() -> None:
    vectorstore = (APP / "modules" / "transformers" / "vectorstore.py").read_text()
    loop = ast.get_source_segment(vectorstore, _function(vectorstore, "_embed_documents_with_retry")) or ""
    assert "get_embedding_gateway()" in loop and ".decide(" in loop, "the batch loop must ask the gateway before it retries"
    probe = ast.get_source_segment(vectorstore, _function(vectorstore, "_build_embedding_model")) or ""
    assert "probe_dimension" in probe, "the dimension probe must go through the gateway"
    for provider in ("cohere", "bedrock", "gemini", "voyage"):
        text = (APP / "services" / "embeddings" / "multimodal" / f"{provider}_provider.py").read_text()
        assert "get_embedding_gateway().call(" in text, f"{provider}: a paid image request must go through the gateway"


def test_no_retrying_sdk_on_an_indexing_factory_path() -> None:
    """``max_retries=<n>`` with n above 0 is not written anywhere on the indexing paths, and the indexing embedding
    factory calls pass ``sdk_retries``. (The model factories' own defaults are for chat; they are overridden by
    ``sdk_retries``, which the call-site contract test reads off the built clients.)"""
    offenders = []
    for root in PAID_CALL_PATHS:
        for path in [root] if root.is_file() else sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_bytes())
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "max_retries":
                    value = node.value
                    if not (isinstance(value, ast.Constant) and value.value in (0, None)):
                        offenders.append(f"{path.relative_to(APP).as_posix()}:{node.value.lineno}: max_retries={ast.unparse(value)}")
    assert not offenders, "\n".join(offenders)
    # The indexing sites build through the region-pinned funnel (``app/utils/llm.py`` build_embeddings, MG-15), which
    # hands sdk_retries to get_embedding_model; a call for the built-in server (config None) has no SDK to retry.
    funnel = ast.parse((APP / "utils" / "llm.py").read_bytes())
    factory = [n for n in ast.walk(funnel) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "get_embedding_model"
               or isinstance(n, ast.Call) and any(getattr(a, "id", "") == "get_embedding_model" for a in n.args)]
    assert factory, "utils/llm.py: build_embeddings no longer reaches get_embedding_model"
    for relative in ("modules/transformers/vectorstore.py", "modules/transformers/entity_vectorstore.py"):
        tree = ast.parse((APP / relative).read_bytes())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") in ("get_embedding_model", "build_embeddings")]
        assert calls, f"{relative}: no embedding factory call found"
        for call in calls:
            builtin = (getattr(call.func, "id", "") == "build_embeddings" and len(call.args) > 1
                       and isinstance(call.args[1], ast.Constant) and call.args[1].value is None)
            if builtin:
                continue
            assert any(k.arg == "sdk_retries" for k in call.keywords), f"{relative}:{call.lineno}: pass sdk_retries"


def _scan(tmp_path: Path, source: str) -> list[tuple[int, str, str]]:
    path = tmp_path / "module.py"
    path.write_text(source)
    return _raw_calls(path)


def test_the_scan_sees_a_raw_call_passed_by_reference(tmp_path: Path) -> None:
    """``asyncio.to_thread(embeddings.embed_documents, texts)`` is a raw paid call that no ``.embed_documents(`` call node shows."""
    found = _scan(tmp_path, "import asyncio\nasync def embed(self, texts):\n    return await asyncio.to_thread(self.e.embed_documents, texts)\n")
    assert [(attr, function) for _line, attr, function in found] == [("embed_documents", "embed")]
    found = _scan(tmp_path, "async def f(loop, m):\n    return await loop.run_in_executor(None, m.invoke, 'x')\n")
    assert [attr for _line, attr, _function in found] == ["invoke"]


def test_the_scan_lets_a_gateway_call_wrap_the_same_references(tmp_path: Path) -> None:
    source = (
        "import asyncio\n"
        "async def embed(self, texts):\n"
        "    return await get_embedding_gateway().call(lambda: asyncio.to_thread(self.e.embed_documents, texts))\n"
        "async def chat(llm, m):\n"
        "    return await get_llm_gateway().invoke(llm, m)\n"
    )
    assert _scan(tmp_path, source) == []


def test_the_scan_covers_the_other_paid_modules() -> None:
    covered = {path.relative_to(APP).as_posix() for path in PAID_CALL_PATHS}
    assert {"utils/indexing_helpers.py", "events/processor.py", "modules/pipeline"} <= covered


def test_the_scan_flags_a_raw_call_evaluated_eagerly_as_a_gateway_argument(tmp_path: Path) -> None:
    """``get_llm_gateway().invoke(llm, llm.invoke(m))`` runs the inner call before the gateway starts: it is raw."""
    found = _scan(tmp_path, "async def f(llm, m):\n    return await get_llm_gateway().invoke(llm, llm.invoke(m))\n")
    assert [attr for _line, attr, _function in found] == ["invoke"]
    deferred = _scan(tmp_path, "async def f(e, t):\n    return await get_embedding_gateway().call(lambda: e.aembed_documents(t))\n")
    assert deferred == []


@pytest.mark.parametrize(
    "source",
    [
        "async def f(m):\n    return await m.ainvoke('x')\n",
        "import functools\nasync def f(m):\n    return await functools.partial(m.ainvoke, 'x')()\n",
        "async def f(m):\n    call = m.ainvoke\n    return await call('x')\n",
        "async def f(m):\n    return await getattr(m, 'ainvoke')('x')\n",
        "async def f(gateway):\n    return await gateway.model.ainvoke('x')\n",
        "async def f(m, other):\n    return await get_llm_gateway().invoke(m, await other.ainvoke('x'))\n",
        "async def f(m, xs):\n    return await m.abatch(xs)\n",
    ],
    ids=["direct", "partial", "alias", "getattr", "receiver-named-gateway", "eager-argument", "abatch"],
)
def test_the_scan_names_every_shape_of_a_raw_paid_call(tmp_path: Path, source: str) -> None:
    assert _scan(tmp_path, source), "a raw paid call the lint does not see"


def test_the_scan_still_accepts_what_goes_through_a_gateway(tmp_path: Path) -> None:
    source = (
        "async def f(llm, m, self):\n"
        "    await get_llm_gateway().invoke(llm, m)\n"
        "    await get_embedding_gateway().call(lambda: llm.aembed_query('x'))\n"
        "    await get_embedding_gateway().call(llm.aembed_query)\n"
        "    await self._gateway.call(lambda: llm.ainvoke(m))\n"
    )
    assert _scan(tmp_path, source) == []
