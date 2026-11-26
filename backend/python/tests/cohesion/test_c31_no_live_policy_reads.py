"""C-31 (QA-1, CPD-6): the worker reads authority from the pin; no durable-path module reads a live policy source.

R2 F2: approvals, the flow rule, the LLM configuration behind ``modelKey``, engine-profile ceilings, model access
and feature switches were re-read while a run executed, so an admin change could widen a run in flight. A pinned
run now reads them from its ``ResolvedRunConfig`` and the narrow-only live layer (control-plane 05 "No policy
reads in Python"), and ``ALLOWED`` is empty. Until the flip a run admitted without a pin still reads them, in one
module (``UNPINNED_MODULE``) that only the selection point imports; a Labs switch read on the durable path must
be one the pin holds, which the readers answer from for a pinned run (``bind_pinned_stages``).

CPD-10 (CPD10-45): ``CONTROL_PLANE_POLICY_STORE=on`` stops Node writing the KV policy documents, so every read of
one, anywhere under ``app``, sits behind the one mode check (``app.harness.policy_documents.policy_documents``):
the documents are read in one function of ``UNPINNED_MODULE`` that asks it first, only the selection point imports
that module, and each selection there asks it before it hands out a reader of a document.
"""

from __future__ import annotations

import ast
import re
from typing import Final

import pytest

from tests.cohesion.support import PYTHON_ROOT, modules

POLICY_NODES: Final = frozenset(
    {"AGENT_HARNESS_APPROVALS", "AGENT_HARNESS_ENGINE_PROFILES", "AGENT_HARNESS_MODEL_ACCESS", "AI_MODELS"}
)
POLICY_PATHS: Final = tuple(
    f"/services/agentHarness/{name}" for name in ("approvals", "engineProfiles", "modelAccess", "memory")
)
MODEL_CONFIG_READERS: Final = frozenset({"get_llm_for_chat", "get_model_config"})
FLOW_SETTINGS: Final = frozenset({"HARNESS_FLOW_GUARD", "FLOW_LEVEL_ENV", "FLOW_ABOVE_ENV"})
SWITCH: Final = re.compile(r"^is_\w+_enabled$")

#: "module::source" -> why it may stay. Empty since CPD-6: a new entry is a run that can be widened in flight.
ALLOWED: dict[str, str] = {}
#: reads that stay live by design: a secret is never pinned, the pin carries only its binding (02-lld §5.2)
EXEMPT: dict[str, str] = {
    "app/harness/credentials/sources.py::AI_MODELS": (
        "how a pinned model is reached (its endpoint and key), read when it is used; which model is the pin's, "
        "checked against the entry by every caller"
    ),
}
#: what a run admitted without a pin reads, as before CPD-6; the flip deletes the module (05 §1 rule 5)
UNPINNED_MODULE: Final = "app/harness/unpinned.py"
UNPINNED: Final = frozenset(
    f"{UNPINNED_MODULE}::{source}"
    for source in (
        "AGENT_HARNESS_APPROVALS",
        "AGENT_HARNESS_ENGINE_PROFILES",
        "AGENT_HARNESS_MODEL_ACCESS",
        "get_llm_for_chat",
    )
)
#: the one module under ``app`` that may import it: where policy is chosen by pin and by the policy store's mode
SELECTION_MODULE: Final = "app/harness/run_policy/select.py"
UNPINNED_IMPORTERS: Final = frozenset({SELECTION_MODULE})
#: the KV documents ``CONTROL_PLANE_POLICY_STORE=on`` stops writing, of those Python reads (CPD-10)
KV_POLICY_NODES: Final = frozenset(
    {"AGENT_HARNESS_APPROVALS", "AGENT_HARNESS_ENGINE_PROFILES", "AGENT_HARNESS_MODEL_ACCESS"}
)
#: where the nodes are named, not read
NODE_DEFINITIONS: Final = "app/config/constants/service.py"
MODE_MODULE: Final = "app/harness/policy_documents.py"
MODE_CHECK: Final = "policy_documents"
KV_READ: Final = "_kv_document"
SETTINGS_MODULE: Final = "app/services/featureflag/platform_settings.py"


def _docstrings(tree: ast.Module) -> set[int]:
    owners = [tree, *(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef))]
    return {
        id(body[0].value)
        for body in (owner.body for owner in owners)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
    }


def _callee(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _sources(node: ast.AST, docstrings: set[int]) -> set[str]:
    if isinstance(node, ast.Attribute) and node.attr in POLICY_NODES:
        return {node.attr}
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
        return {path for path in POLICY_PATHS if node.value.startswith(path)}
    if isinstance(node, ast.Call):
        callee = _callee(node)
        if callee in MODEL_CONFIG_READERS or (callee is not None and SWITCH.match(callee)):
            return {callee}
        names = {a.id for a in node.args if isinstance(a, ast.Name)}
        return names & FLOW_SETTINGS
    return set()


def live_policy_reads(module: str, text: str) -> set[str]:
    tree = ast.parse(text)
    docstrings = _docstrings(tree)
    return {f"{module}::{source}" for node in ast.walk(tree) for source in _sources(node, docstrings)}


@pytest.fixture(scope="module")
def reads() -> set[str]:
    found: set[str] = set()
    for path in modules("app/harness"):
        found |= live_policy_reads(path.relative_to(PYTHON_ROOT).as_posix(), path.read_text(encoding="utf-8"))
    return found


def _labs_reader_flags() -> dict[str, str]:
    """Each Labs reader (``is_x_enabled``) -> the flag it reads, from the readers' own source."""
    tree = ast.parse((PYTHON_ROOT / SETTINGS_MODULE).read_text(encoding="utf-8"))
    flags: dict[str, str] = {}
    for fn in (n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and SWITCH.match(n.name)):
        for call in (c for c in ast.walk(fn) if isinstance(c, ast.Call) and _callee(c) == "_platform_flag"):
            flags[fn.name] = next(a.attr for a in call.args if isinstance(a, ast.Attribute))
    return flags


def _switches(found: set[str]) -> set[str]:
    return {read for read in found if SWITCH.match(read.split("::")[1])}


def test_c31_no_live_policy_read_on_the_durable_path(reads: set[str]) -> None:
    assert reads - _switches(reads) - set(ALLOWED) - set(EXEMPT) - UNPINNED == set(), (
        "read it from the pinned ResolvedRunConfig, or narrow through the live layer"
    )


def test_c31_the_allow_list_is_empty_and_every_listed_read_still_exists(reads: set[str]) -> None:
    assert ALLOWED == {}
    assert (set(EXEMPT) | UNPINNED) - reads == set()


def test_c31_every_labs_switch_the_durable_path_reads_is_one_the_pin_holds(reads: set[str]) -> None:
    from harness_core.policy import policy_registry

    pinned = {d.path.removeprefix("rollout.") for d in policy_registry().all() if d.path.startswith("rollout.")}
    readers = _labs_reader_flags()
    called = {read.split("::")[1] for read in _switches(reads)}
    assert called, "the durable path reads no Labs switch? C-31's scan is broken"
    assert called <= set(readers), "a switch that is not a Labs reader: pin it or read it from the pin"
    assert {readers[name] for name in called} <= pinned, "add the flag as a rollout dimension so admission pins it"


def unpinned_importers(module: str, text: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        names = [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        names += [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
        if any(name == "app.harness.unpinned" or name.startswith("app.harness.unpinned.") for name in names):
            found.add(module)
        if isinstance(node, ast.ImportFrom) and node.module == "app.harness" and any(a.name == "unpinned" for a in node.names):
            found.add(module)
    return found


def test_c31_only_the_selection_point_imports_the_unpinned_reads() -> None:
    importers: set[str] = set()
    # All of ``app`` since CPD-10: today's loop's flow guard, the agent routes and the model gateway choose there too.
    for path in modules("app"):
        text = path.read_text(encoding="utf-8-sig")
        # Parsed only where the module is named: the tree is thousands of files.
        if "unpinned" in text:
            importers |= unpinned_importers(path.relative_to(PYTHON_ROOT).as_posix(), text)
    assert importers == UNPINNED_IMPORTERS


# CPD10-45


def _functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [call for call in ast.walk(node) if isinstance(call, ast.Call) and _callee(call) == name]


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def kv_policy_reads(module: str, text: str) -> set[str]:
    """Every mention of a KV policy document in ``module``: its node, or its path as a literal."""
    tree = ast.parse(text)
    docstrings = _docstrings(tree)
    kv_paths = tuple(path for path in POLICY_PATHS if not path.endswith("/memory"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in KV_POLICY_NODES:
            found.add(f"{module}::{node.attr}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            found |= {f"{module}::{path}" for path in kv_paths if node.value.startswith(path)}
    return found


def ungated_kv_reads(text: str) -> list[str]:
    """What in the unpinned module reaches a KV policy document other than through the one gated read: a node
    named outside a ``KV_READ`` call, a configuration read outside ``KV_READ``, or a ``KV_READ`` that does not
    ask the mode first."""
    tree = ast.parse(text)
    problems: list[str] = []
    gated = {
        id(inner)
        for call in _calls(tree, KV_READ)
        for arg in call.args
        for inner in ast.walk(arg)
    }
    problems += [
        f"{node.attr} named outside {KV_READ}()"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in KV_POLICY_NODES and id(node) not in gated
    ]
    readers = [fn for fn in _functions(tree) if fn.name == KV_READ]
    if len(readers) != 1:
        return [*problems, f"{len(readers)} definitions of {KV_READ}"]
    inside = {id(inner) for inner in ast.walk(readers[0])}
    problems += [f"get_config at line {call.lineno} outside {KV_READ}" for call in _calls(tree, "get_config") if id(call) not in inside]
    checks, reads = _calls(readers[0], MODE_CHECK), _calls(readers[0], "get_config")
    if not checks or not reads or min(c.lineno for c in checks) >= min(r.lineno for r in reads):
        problems.append(f"{KV_READ} reads before it asks {MODE_CHECK}()")
    return problems


def kv_readers(text: str) -> set[str]:
    """The unpinned module's top-level names that reach ``KV_READ``, directly or through one another."""
    tree = ast.parse(text)
    top = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.name != KV_READ
    }
    reaching = {name for name, node in top.items() if _calls(node, KV_READ)}
    while True:
        more = {name for name, node in top.items() if name not in reaching and _names(node) & reaching}
        if not more:
            return reaching
        reaching |= more


def unselected_readers(text: str, readers: set[str]) -> list[str]:
    """Functions of the selection module that hand out a KV reader without asking the mode."""
    return sorted(
        f"{fn.name} uses {', '.join(sorted(_names(fn) & readers))}"
        for fn in _functions(ast.parse(text))
        if _names(fn) & readers and not _calls(fn, MODE_CHECK)
    )


def test_cpd10_45_a_kv_policy_document_is_named_in_the_unpinned_module_only() -> None:
    found: set[str] = set()
    for path in modules("app", "harness_core", "harness_engines"):
        module = path.relative_to(PYTHON_ROOT).as_posix()
        text = path.read_text(encoding="utf-8-sig")
        if module != NODE_DEFINITIONS and ("AGENT_HARNESS_" in text or "/services/agentHarness/" in text):
            found |= kv_policy_reads(module, text)
    assert {read.split("::")[0] for read in found} == {UNPINNED_MODULE}
    assert {read.split("::")[1] for read in found} == KV_POLICY_NODES, "a document nobody reads: drop it from the list"


def test_cpd10_45_every_kv_policy_read_asks_the_mode_first() -> None:
    assert ungated_kv_reads((PYTHON_ROOT / UNPINNED_MODULE).read_text(encoding="utf-8")) == []


def test_cpd10_45_every_selection_of_a_kv_reader_asks_the_mode() -> None:
    readers = kv_readers((PYTHON_ROOT / UNPINNED_MODULE).read_text(encoding="utf-8"))
    assert {"ConfiguredApprovals", "ConfiguredEngineCeilings", "ConfiguredOrgAccessPolicies", "UnpinnedRunPolicy"} <= readers
    assert unselected_readers((PYTHON_ROOT / SELECTION_MODULE).read_text(encoding="utf-8"), readers) == []


def test_cpd10_45_the_mode_is_read_from_the_environment_in_one_function() -> None:
    # That no other module reads the flag is C-16's (``test_cpd10_45_the_policy_store_mode_lists_its_python_reader``).
    tree = ast.parse((PYTHON_ROOT / MODE_MODULE).read_text(encoding="utf-8"))
    getters = [fn.name for fn in _functions(tree) if _calls(fn, "getenv")]
    assert getters == ["policy_store_mode"]
    mode = next(fn for fn in _functions(tree) if fn.name == "policy_store_mode")
    assert any(isinstance(d, ast.Name) and d.id == "cache" for d in mode.decorator_list), "read once per process"


SAMPLE: Final = '''
"""Mentions /services/agentHarness/approvals in prose only."""
policy = await config.get_config(config_node_constants.AGENT_HARNESS_APPROVALS.value)
memory = await config.get_config("/services/agentHarness/memory/org-1")
llm = await get_llm_for_chat(config, key)
on = await flags.is_memory_enabled(config)
level = os.getenv(FLOW_LEVEL_ENV)
'''


UNGATED_SAMPLE: Final = '''
async def _kv_document(config_service, node, **read):
    document = await config_service.get_config(node.value, raise_on_error=True, **read)
    if policy_documents() != KV:
        raise ResolvedPolicyRequired
    return document

class Direct:
    async def policy(self):
        return await self._config.get_config(config_node_constants.AGENT_HARNESS_APPROVALS.value)

class Gated:
    async def policy(self):
        return await _kv_document(self._config, config_node_constants.AGENT_HARNESS_MODEL_ACCESS)

def through(config):
    return Gated(config)

def pure(policy):
    return policy.flows
'''

UNSELECTED_SAMPLE: Final = '''
def chosen(config, live):
    if policy_documents() == KV:
        return Gated(config)
    return Live(live())

def unchosen(config):
    return through(config)

def unrelated(policy):
    return pure(policy)
'''


def test_cpd10_45_the_scans_find_a_planted_read_outside_the_mode_check() -> None:
    assert ungated_kv_reads(UNGATED_SAMPLE) == [
        "AGENT_HARNESS_APPROVALS named outside _kv_document()",
        "get_config at line 10 outside _kv_document",
        "_kv_document reads before it asks policy_documents()",
    ]
    assert kv_readers(UNGATED_SAMPLE) == {"Gated", "through"}
    assert unselected_readers(UNSELECTED_SAMPLE, {"Gated", "through"}) == ["unchosen uses through"]
    assert kv_policy_reads("sample.py", SAMPLE) == {"sample.py::AGENT_HARNESS_APPROVALS"}
    assert kv_policy_reads("sample.py", 'path = "/services/agentHarness/engineProfiles"') == {
        "sample.py::/services/agentHarness/engineProfiles"
    }
    assert unpinned_importers("sample.py", "from app.harness.unpinned import ConfiguredApprovals") == {"sample.py"}


def test_c31_the_scan_finds_each_kind_of_read() -> None:
    assert {read.split("::")[1] for read in live_policy_reads("sample.py", SAMPLE)} == {
        "AGENT_HARNESS_APPROVALS",
        "/services/agentHarness/memory",
        "get_llm_for_chat",
        "is_memory_enabled",
        "FLOW_LEVEL_ENV",
    }
