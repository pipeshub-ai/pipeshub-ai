"""C-3 to C-7: generic packages stand alone; the catalog, fold, refusals and worker features agree.

C-3  Generic packages import with the app and its frameworks blocked (Loom).
C-4  The event catalog changes only deliberately (ARCHITECTURE §4.8: existing types first).
C-5  One fold version across the worker, Node and the pinned golden spec.
C-6  Refusal codes are unique, so a log line or ``errorType`` names one cause.
C-7  Node hands runs only to features the Python worker advertises.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

from harness_core.workspace.sync.types import WorkspaceSyncRefused
from tests.cohesion.support import NODE_HARNESS, PYTHON_ROOT, REPO_ROOT, dotted, modules

BLOCKED = ("app", "docker", "fastapi", "dependency_injector")
# Modules that need an optional extra the bare distribution does not install.
NEEDS_EXTRA = {"harness_core.sandbox.remote"}


def test_c3_every_generic_module_imports_without_the_app() -> None:
    names = sorted(
        {
            dotted(path)
            for path in modules("harness_core", "harness_engines", include_testing=True)
            if not any(dotted(path).startswith(extra) for extra in NEEDS_EXTRA)
            and "models.transports" not in dotted(path)
        }
    )
    script = f"""
import importlib, importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {BLOCKED!r}:
            raise ImportError(f"blocked: {{name}}")
        return None
sys.meta_path.insert(0, Block())
failed = []
for name in {names!r}:
    try:
        importlib.import_module(name)
    except Exception as exc:
        failed.append(f"{{name}}: {{exc}}")
print("\\n".join(failed))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=PYTHON_ROOT, capture_output=True, text=True, timeout=300, check=False
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "", f"imports the app or a framework:\n{result.stdout}"


def test_c4_the_event_catalog_matches_the_reviewed_snapshot() -> None:
    from harness_core.contracts.generated.event_catalog import EVENT_WRITERS

    current = {t: sorted(w) for t, w in sorted(EVENT_WRITERS.items())}
    reviewed = json.loads((PYTHON_ROOT / "tests" / "cohesion" / "event_catalog.json").read_text())
    added, removed = sorted(current.keys() - reviewed.keys()), sorted(reviewed.keys() - current.keys())
    changed = sorted(t for t in current.keys() & reviewed.keys() if current[t] != reviewed[t])
    assert (added, removed, changed) == ([], [], []), (
        "event catalog changed: reuse an existing type where one fits (ARCHITECTURE §4.8), then update "
        f"tests/cohesion/event_catalog.json. added={added} removed={removed} writers changed={changed}"
    )


def test_c5_one_fold_version_everywhere() -> None:
    from harness_core.driver.capabilities import worker_capabilities
    from harness_core.kernel.fold import FOLD_VERSION

    node = re.search(r"export const RUN_SPEC_FOLD_VERSION = (\d+);", (NODE_HARNESS / "domain" / "run-spec.ts").read_text())
    assert node is not None
    golden = json.loads((REPO_ROOT / "contracts" / "harness" / "vectors" / "pinned-run-spec.json").read_text())
    assert {
        "worker": FOLD_VERSION,
        "advertised": worker_capabilities().fold_version,
        "node": int(node.group(1)),
        "golden": golden["pinned"]["spec"]["foldVersion"],
    } == dict.fromkeys(("worker", "advertised", "node", "golden"), FOLD_VERSION)


def _subclasses(base: type) -> list[type]:
    found, todo = [], list(base.__subclasses__())
    while todo:
        cls = todo.pop()
        found.append(cls)
        todo.extend(cls.__subclasses__())
    return found


def test_c6_refusal_codes_are_unique() -> None:
    import harness_core.artifacts.errors as artifact_errors
    import harness_core.engines.errors as engine_errors
    import harness_core.gateway.errors as gateway_errors
    import harness_core.sandbox.errors as sandbox_errors
    from harness_core.driver import RunRefused
    from harness_core.journal.children import ChildRefusal
    from harness_core.sandbox.egress.errors import EgressRefusalReason

    codes: dict[str, list[str]] = {}
    for cls in _subclasses(sandbox_errors.SandboxError):
        if "code" in vars(cls):
            codes.setdefault(vars(cls)["code"], []).append(f"sandbox.{cls.__name__}")
    for cls in [*_subclasses(engine_errors.EngineRefusal), *_subclasses(engine_errors.EngineGone)]:
        if "reason_code" in vars(cls):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"engines.{cls.__name__}")
    for cls in _subclasses(RunRefused):
        if "code" in vars(cls):
            codes.setdefault(vars(cls)["code"], []).append(f"driver.{cls.__name__}")
    assert "executor_unavailable" in codes
    for cls in _subclasses(artifact_errors.ArtifactPublishRefused):
        if "reason_code" in vars(cls):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"artifacts.{cls.__name__}")
    for reason in EgressRefusalReason:
        codes.setdefault(reason.value, []).append(f"egress.{reason.name}")
    for cls in _subclasses(WorkspaceSyncRefused):
        if "reason_code" in vars(cls):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"workspace.sync.{cls.__name__}")
    assert "unsafe_member" in codes
    for cls in _subclasses(gateway_errors.GatewayRefusal):
        if "reason_code" in vars(cls):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"gateway.{cls.__name__}")
    import harness_core.access.errors as access_errors

    for cls in _subclasses(access_errors.ModelAccessRefused):
        if "reason_code" in vars(cls):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"access.{cls.__name__}")
    import harness_core.budget.errors as budget_errors

    for cls in _subclasses(budget_errors.BudgetRefused):
        if "reason_code" in vars(cls):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"budget.{cls.__name__}")
    assert "budget_exhausted" in codes
    import harness_core.credentials.errors as credential_errors

    for cls in _subclasses(credential_errors.CredentialRefused):
        if "reason_code" in vars(cls):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"credentials.{cls.__name__}")
    assert "credential_revoked" in codes
    from harness_core.sandbox.mcp_runner.errors import MCPRunnerRefused

    for cls in _subclasses(MCPRunnerRefused):
        if "reason_code" in vars(cls):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"mcp_runner.{cls.__name__}")
    assert "runner_owner_mismatch" in codes
    from harness_core.mcp_hub.errors import MCPHubRefused

    for cls in _subclasses(MCPHubRefused):
        if "reason_code" in vars(cls):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"mcp_hub.{cls.__name__}")
    assert "mcp_tool_unapproved" in codes
    from harness_core.tool_identity.errors import ToolIdentityRefused

    for cls in [ToolIdentityRefused, *_subclasses(ToolIdentityRefused)]:
        if "reason_code" in vars(cls):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"tool_identity.{cls.__name__}")
    assert "tool_instance_ambiguous" in codes
    for code in gateway_errors.TOKEN_REFUSAL_CODES:
        codes.setdefault(code, []).append("gateway.TokenRefused")
    for refusal in ChildRefusal:
        codes.setdefault(refusal.value, []).append(f"children.{refusal.name}")
    assert "child_grant_too_large" in codes
    from harness_core.workspace.types import WorkspaceError

    for cls in _subclasses(WorkspaceError):
        if "reason_code" in vars(cls) and not issubclass(cls, WorkspaceSyncRefused):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"workspace.{cls.__name__}")
    assert "workspace_writer_busy" in codes
    from harness_core.knowledge.errors import PublicationRefused

    for cls in _subclasses(PublicationRefused):
        if "reason_code" in vars(cls):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"knowledge.{cls.__name__}")
    assert "publication_answer_only" in codes
    from harness_core.models.errors import ModelPolicyRefused

    for cls in [ModelPolicyRefused, *_subclasses(ModelPolicyRefused)]:
        if "reason_code" in vars(cls):
            codes.setdefault(vars(cls)["reason_code"], []).append(f"models.{cls.__name__}")
    assert "model_route_not_pinned" in codes
    from app.harness.legacy.errors import RESOLVED_TURN_REFUSAL_CODES

    for code in RESOLVED_TURN_REFUSAL_CODES:
        codes.setdefault(code, []).append("legacy.ResolvedTurnRefused")
    assert "resolved_required" in codes
    clashes ={code: owners for code, owners in codes.items() if len(owners) > 1}
    assert clashes == {}, f"one code, several refusals: {clashes}"


def test_c7_node_knows_exactly_the_features_the_worker_advertises() -> None:
    """Node's list is the generated ``KNOWN_WORKER_FEATURES`` (QA7-1); Python advertises exactly it."""
    from app.harness.contracts.generated.pipeshub.enums import KNOWN_WORKER_FEATURES
    from app.harness.worker.capabilities import WORKER_CAPABILITIES

    source = (NODE_HARNESS / "domain" / "worker-compat.ts").read_text()
    assert "KNOWN_WORKER_FEATURES,\n" in source and "contracts/generated/ext/pipeshub/enums" in source
    assert "KNOWN_WORKER_FEATURES = [" not in source, "Node keeps its own copy of the worker features"
    assert set(KNOWN_WORKER_FEATURES) == {f.root for f in WORKER_CAPABILITIES.features}
