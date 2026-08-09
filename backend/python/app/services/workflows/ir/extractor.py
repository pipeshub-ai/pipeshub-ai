"""AST-to-IR extractor for code workflows.

Parses a workflow source file and extracts a WorkflowIR graph
representing the workflow's structure. Used by the codegen loop to
validate and store the IR alongside the generated source.

Also exports `extract_trigger_specs` which pulls `@workflow(triggers=[...])`
declarations into trigger-spec dicts that `WorkflowManageTool._run_codegen`
converts to `TaskTrigger` rows.
"""
from __future__ import annotations

import ast
import hashlib
import logging
from typing import Any

from app.services.workflows.ast_names import callable_name, decorator_name
from app.services.workflows.domain.models import (
    IREdge,
    IRNode,
    IRNodeKind,
    WorkflowIR,
)

__all__ = ["extract_ir", "extract_trigger_specs", "agent_pins_from_ir", "tool_pins_from_ir"]

logger = logging.getLogger(__name__)


def extract_ir(source: str) -> WorkflowIR:
    """Parse workflow source and return a WorkflowIR graph.

    Extracts @workflow and @step decorated functions, builds nodes and
    edges representing the call structure where determinable statically.
    Falls back gracefully for patterns it cannot resolve.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return WorkflowIR()

    extractor = _IRExtractor(source)
    extractor.visit(tree)
    return extractor.build()


def tool_pins_from_ir(ir: WorkflowIR) -> dict[str, str]:
    """Normalised `Tool.name` -> the literal the source passes to `ctx.tool()`.

    This is the whole tool surface of a version's source: the verifier rejects
    a non-literal tool name (`DYNAMIC_TOOL_NAME`), so every reachable tool call
    appears here as a TOOL_CALL node. `compute_run_grant` uses it to pin a run
    that declares no `tool_names` of its own, instead of granting whatever the
    run's registry resolved.
    """
    from app.services.workflows.interface.broker import normalize_tool_name

    pins: dict[str, str] = {}
    for node in ir.nodes:
        if node.kind != IRNodeKind.TOOL_CALL:
            continue
        raw = str(node.metadata.get("tool_path") or "")
        if not raw:
            continue
        normalized = normalize_tool_name(raw)
        if normalized:
            pins[normalized] = raw
    return pins


def agent_pins_from_ir(ir: WorkflowIR) -> set[str]:
    """Agent ids the source passes to `ctx.agent()`.

    The AGENT_RUN counterpart to `tool_pins_from_ir`: without it the grant's
    `agent_ids` is empty, which the broker reads as "any agent in this org", so
    a workflow could drive an agent it was never written to call.

    A non-literal agent id yields an empty `tool_path` and is skipped, which
    would silently drop the pin and re-widen the grant -- `_lint_agent_grant`
    in the verifier is what stops that source from committing.
    """
    return {
        raw
        for node in ir.nodes
        if node.kind == IRNodeKind.AGENT_CALL
        and (raw := str(node.metadata.get("tool_path") or ""))
    }


class _IRExtractor(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self._source = source
        self._nodes: list[IRNode] = []
        self._edges: list[IREdge] = []
        self._entry_node_id: str | None = None
        self._step_node_ids: dict[str, str] = {}  # fn_name -> node_id

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind, meta = _classify_function(node)
        if kind is None:
            self.generic_visit(node)
            return

        node_id = _stable_id(node.name, node.lineno, kind.value)
        ir_node = IRNode(
            node_id=node_id,
            kind=kind,
            label=node.name,
            source_start=node.lineno,
            source_end=getattr(node, "end_lineno", None) or node.lineno,
            metadata=meta,
        )
        self._nodes.append(ir_node)

        if kind == IRNodeKind.WORKFLOW:
            self._entry_node_id = node_id
        elif kind == IRNodeKind.STEP:
            self._step_node_ids[node.name] = node_id

        # Extract tool/agent calls within the function body
        for child in ast.walk(node):
            if isinstance(child, ast.Await):
                call_node_id = _maybe_extract_call(child, self._nodes)
                if call_node_id:
                    self._edges.append(IREdge(from_node=node_id, to_node=call_node_id))

        self.generic_visit(node)

    def build(self) -> WorkflowIR:
        # Wire step call edges: workflow → steps called within
        # (static resolution is best-effort; dynamic calls are left unresolved)
        for node in self._nodes:
            if node.kind == IRNodeKind.WORKFLOW and self._step_node_ids:
                for step_name, step_id in self._step_node_ids.items():
                    edge = IREdge(from_node=node.node_id, to_node=step_id, label="calls")
                    if edge not in self._edges:
                        self._edges.append(edge)

        return WorkflowIR(
            nodes=self._nodes,
            edges=self._edges,
            entry_node_id=self._entry_node_id,
        )


def _stable_id(name: str, position: int, kind: str, column: int = 0) -> str:
    """Content-addressed node ID — same inputs always produce the same ID.

    `position` is a 1-based source line; `column` disambiguates two calls to
    the same target on one line. Callers must pass a line (not a column) or
    every call to the same tool collapses into one graph node.
    """
    raw = f"{kind}:{name}:{position}:{column}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _classify_function(
    node: ast.AsyncFunctionDef,
) -> tuple[IRNodeKind | None, dict[str, Any]]:
    """Return (IRNodeKind, metadata) for a decorated async function, or (None, {})."""
    for d in node.decorator_list:
        name = decorator_name(d)
        kwargs: dict[str, Any] = {}
        if isinstance(d, ast.Call):
            for kw in d.keywords:
                if kw.arg and isinstance(kw.value, ast.Constant):
                    kwargs[kw.arg] = kw.value.value

        if name == "workflow":
            return IRNodeKind.WORKFLOW, {"workflow_name": kwargs.get("name", node.name)}
        if name == "step":
            return IRNodeKind.STEP, {
                "retries": kwargs.get("retries", 0),
                "timeout_s": kwargs.get("timeout_s"),
                "side_effect": kwargs.get("side_effect", "none"),
            }

    return None, {}


# ---------------------------------------------------------------------------
# Trigger extraction
# ---------------------------------------------------------------------------

def extract_trigger_specs(source: str) -> list[dict[str, Any]]:
    """Parse workflow source and return trigger spec dicts extracted from
    ``@workflow(triggers=[...])``.

    Supported helper calls in the triggers list:
    - ``cron("EXPR", tz="TZ")``  → kind=cron
    - ``interval(seconds=N)``    → kind=interval
    - ``once_at("ISO")``         → kind=one_time
    - ``on_event(TYPE, **kw)``   → kind=event

    Unknown helpers are skipped with a debug log so unrecognised calls
    never break workflow creation.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or decorator_name(dec) != "workflow":
                continue
            # Found @workflow(...)
            for kw in dec.keywords:
                if kw.arg != "triggers":
                    continue
                if not isinstance(kw.value, ast.List):
                    continue
                specs: list[dict[str, Any]] = []
                for elt in kw.value.elts:
                    spec = _parse_trigger_call(elt)
                    if spec is not None:
                        specs.append(spec)
                return specs
    return []


def _parse_trigger_call(node: ast.expr) -> dict[str, Any] | None:
    """Convert one trigger helper call AST node to a spec dict."""
    if not isinstance(node, ast.Call):
        return None

    name = callable_name(node.func)
    positional = [_eval_const(a) for a in node.args]
    kws = {kw.arg: _eval_const(kw.value) for kw in node.keywords if kw.arg}

    if name == "cron":
        expr = positional[0] if positional else kws.get("expression") or kws.get("expr")
        if not isinstance(expr, str):
            logger.debug("extract_trigger_specs: cron() missing expression argument")
            return None
        tz = kws.get("tz") or kws.get("timezone") or "UTC"
        return {"kind": "cron", "cron_expression": expr, "timezone": tz}

    if name == "interval":
        seconds = kws.get("seconds") or kws.get("interval_seconds") or (positional[0] if positional else None)
        if not isinstance(seconds, (int, float)):
            logger.debug("extract_trigger_specs: interval() missing seconds argument")
            return None
        return {"kind": "interval", "interval_seconds": int(seconds)}

    if name == "once_at":
        fire_at = positional[0] if positional else kws.get("at") or kws.get("fire_at")
        if not isinstance(fire_at, str):
            logger.debug("extract_trigger_specs: once_at() missing datetime argument")
            return None
        return {"kind": "one_time", "fire_at": fire_at}

    if name == "on_event":
        event_type = _resolve_event_type(node.args[0] if node.args else None)
        if not event_type:
            logger.debug("extract_trigger_specs: on_event() missing event type")
            return None
        event_filter: dict[str, Any] = {"event_type": event_type}
        event_filter.update({k: v for k, v in kws.items() if v is not None})
        return {"kind": "event", "event_filter": event_filter}

    logger.debug("extract_trigger_specs: unknown trigger helper %r — skipping", name)
    return None


def _eval_const(node: ast.expr) -> Any:
    """Return a Python literal from an AST constant/name. Returns None for complex nodes."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return -node.operand.value
    return None


def _resolve_event_type(node: ast.expr | None) -> str | None:
    """Resolve ``slack.MessagePosted`` or ``"slack.message.posted"`` to the
    catalog's canonical event type, matching what the SDK helper produces at
    runtime."""
    from app.services.workflows.sdk.triggers import canonical_event_type

    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Attribute):
        source = callable_name(node.value)
        if source:
            return canonical_event_type(source, node.attr)
    return None


def _maybe_extract_call(
    await_node: ast.Await,
    nodes: list[IRNode],
) -> str | None:
    """If the awaited call is ctx.tool or ctx.agent, add an IR node and return its id."""
    call = await_node.value
    if not isinstance(call, ast.Call):
        return None

    func = call.func
    if not isinstance(func, ast.Attribute):
        return None

    # `await ctx.agent("id").run(...)`: the awaited call is `.run`, and the
    # `ctx.agent` call it is chained onto carries the id. Missing it would
    # leave the version with no agent pin, which the grant reads as "granted
    # no agents" and denies at run time.
    if isinstance(func.value, ast.Call):
        call = func.value
        func = call.func
        if not isinstance(func, ast.Attribute):
            return None

    if not isinstance(func.value, ast.Name) or func.value.id != "ctx":
        return None

    method = func.attr
    if method not in ("tool", "agent"):
        return None

    tool_path = ""
    if call.args and isinstance(call.args[0], ast.Constant):
        tool_path = str(call.args[0].value)

    kind = IRNodeKind.TOOL_CALL if method == "tool" else IRNodeKind.AGENT_CALL
    node_id = _stable_id(
        tool_path or method, await_node.lineno, kind.value, await_node.col_offset,
    )
    if any(existing.node_id == node_id for existing in nodes):
        return node_id
    nodes.append(IRNode(
        node_id=node_id,
        kind=kind,
        label=tool_path or method,
        source_start=await_node.lineno,
        source_end=getattr(await_node, "end_lineno", None) or await_node.lineno,
        metadata={"tool_path": tool_path},
    ))
    return node_id
