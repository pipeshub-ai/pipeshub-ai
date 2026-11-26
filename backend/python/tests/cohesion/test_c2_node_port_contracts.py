"""C-2 for Node (QA-19, CON-12): every Node port with an in-memory adapter has a shared contract suite, and every
class that implements the port runs it.

A port is an interface declared under ``src/modules/<module>/ports/`` that an ``InMemory*`` class ``implements``: in
``src``, or in the control plane's test support, where its in-memory adapters live (NFR-45, R3 M14). Its suite is a ``describe…Contract`` function in a test root's ``**/support/*-contract.ts``; an adapter runs it when a test
file calls that function (directly or through another suite it calls) and names the adapter's class, itself or
through a support file it imports. The control-plane module is scanned too, so CPD's ports join this registry once
they land rather than a parallel one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

import pytest

from tests.cohesion.support import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path

NODE_APP = REPO_ROOT / "backend" / "nodejs" / "apps"
MODULES = ("agent-harness", "control-plane")

# The app's test tree, then any ``<dir>/tests`` beside it: code kept in a folder of its own (an edition's) keeps its
# suites and the tests that run them there, and its adapters of these ports count only if those are read too.
TEST_ROOTS = (
    NODE_APP / "tests",
    *sorted(p for p in NODE_APP.glob("*/tests") if p.is_dir() and p.parent.name != "node_modules"),
)
# In such a root, the ports its own adapters bring, with their suites: {"ports": {port: suite}, "exempt": {port: why}}.
REGISTRY_OVERLAY = "c2-node-ports.json"

# port interface -> its shared suite
NODE_PORTS: dict[str, str] = {
    # WP-73: one person's unfinished runs (SEC-AZ-08, DEACT-12), the audit search's run directory (J-18) and the
    # actor's group membership (G-41).
    "ActorRuns": "describeExecutionStoreContract",
    "OperatorRuns": "describeExecutionStoreContract",
    # ADR-UI-01 D1, D2 (UI-B1): a person's runs and the chat list's facts; the seen marks on the conversation.
    "RunLists": "describeExecutionStoreContract",
    "ConversationSeenMarks": "describeConversationSeenContract",
    "ActorGroupMembership": "describeActorGroupMembershipContract",
    "AgentErasureQueue": "describeAgentErasureQueueContract",
    "AuditAnchorSink": "describeAuditAnchorSinkContract",
    "AuditChainStore": "describeAuditChainStoreContract",
    # AUDITUI: the admin pages' reads and the anchor lookup (LONGTASK-EVAL merge of FIXPACK14).
    "AuditAnchorLookup": "describeAuditAnchorLookupContract",
    "AuditChainQueries": "describeAuditChainQueriesContract",
    "ControlPlaneLiveness": "describeControlPlaneLivenessContract",
    "DataKeyStore": "describeDataKeyStoreContract",
    "ErasureJobStore": "describeErasureJobStoreContract",
    "JournalTail": "describeJournalTailContract",
    "LiveBus": "describeLiveBusContract",
    "OrgCatalogRows": "describeCatalogStoreContract",
    "LiveBusEraser": "describeLiveBusContract",
    "PayloadReferences": "describePayloadReferencesContract",
    "PayloadStore": "describePayloadStoreContract",
    "TransactionalPayloadStore": "describeTransactionalPayloadStoreContract",
    "RunReadyNotifier": "describeRunReadyNotifierContract",
    "RunWakeup": "describeRunWakeupContract",
    "WorkspaceObjectBytes": "describeWorkspaceObjectBytesContract",
    "WorkerFeatureSightings": "describeWorkerFeatureSightingsContract",
    "ApproverDirectory": "describeApproverDirectoryContract",
    "ConversationEraser": "describeConversationEraserContract",
    "ConversationRetention": "describeConversationRetentionContract",
    "ConversationStateLookup": "describeConversationStateLookupContract",
    "BackfillSource": "describeBackfillSourceContract",
    "BudgetLedgerStore": "describeBudgetLedgerContract",
    "ChildRunStore": "describeChildRunContract",
    "ErasureBackfillStore": "describeErasureBackfillStoreContract",
    "ExecutionStore": "describeExecutionStoreContract",
    "JournalStore": "describeJournalStoreContract",
    "MemoryEraser": "describeMemoryStoreContract",
    "MemoryStore": "describeMemoryStoreContract",
    "PendingReaps": "describePendingReapsContract",
    "ScheduleEraser": "describeScheduleStoreContract",
    "ScheduleStore": "describeScheduleStoreContract",
    "SessionForkStore": "describeSessionForkContract",
    "SessionRoleLookup": "describeCollaboratorTurnsContract",
    "WorkspaceKeyStore": "describeWorkspaceStoresContract",
    "WorkspaceRepository": "describeWorkspaceStoresContract",
    "WorkspaceWriterStore": "describeWorkspaceStoresContract",
    "CodingEnginePreferences": "describeCodingEnginePreferencesContract",
    # MEMSW: the person's switch lives with their engine choice; CP-7 of that contract holds it.
    "PersonalMemoryChoice": "describeCodingEnginePreferencesContract",
    # The control plane (03-interfaces §2-§5, CP-SKEL): in-memory adapters in test support; the WP named in
    # The same suite runs on each production adapter.
    "ControlPlaneTransaction": "transactionContract",
    "ChangeOutbox": "transactionContract",
    "InstallationStore": "describeInstallationStoreContract",
    "GrantStore": "describeGrantStoreContract",
    "PolicyStore": "describePolicyStoreContract",
    "PolicyLayers": "describePolicyStoreContract",
    "PolicyStorePurge": "describePolicyStorePurgeContract",
    "MigrationLedger": "describeMigrationLedgerContract",
    "LegacyPolicySource": "describeLegacyPolicySourceContract",
    "BindingStore": "describeBindingStoreContract",
    "ConfigValidator": "describeDomainModuleContract",
    "DescriptorProvider": "describeDomainModuleContract",
    "ApprovalTriggers": "describeDomainModuleContract",
    "BindingSelector": "describeDomainModuleContract",
    "DimensionContributor": "describeDomainModuleContract",
    "InstallExpander": "describeDomainModuleContract",
    "PolicyLayerSource": "describePolicyLayerSourceContract",
    "EffectiveSetResolver": "describeEffectiveSetResolverContract",
    "CapabilityLedger": "describeCapabilityLedgerContract",
    "RunConfigPinner": "describeRunConfigPinnerContract",
    "SnapshotSource": "describeSnapshotSourceContract",
    "HeadRevisions": "describeHeadRevisionsContract",
    # Moved with the org head by an AI-model save (CPD-6): HR-3 of the same suite holds both adapters to it.
    "DeploymentRevision": "describeHeadRevisionsContract",
    "Authorizer": "describeAuthorizerContract",
    "CredentialBinder": "describeCredentialBinderContract",
    "BindingStateView": "describeCredentialBinderContract",
    "ChangeNotifier": "describeChangeNotifierContract",
    "MembershipPort": "describeDirectoryContract",
    "AgentDirectory": "describeDirectoryContract",
    "ReferenceChecker": "describeReferenceCheckerContract",
    # CPD-9: the facts the authorizer reads.
    "OrgRoles": "describeOrgRolesContract",
    "DelegationSource": "describeDelegationSourceContract",
    # SIGNIN: a person's own Claude sign-in record and the org's switch.
    "EngineSignInStore": "describeEngineSignInStoreContract",
    "SandboxSnapshotIndex": "describeSandboxSnapshotIndexContract",
}

# port interface -> why it has an in-memory adapter but no shared suite
NODE_EXEMPT: dict[str, str] = {
    "AgentDeletionLookup": (
        "its production class is an HTTP client of the Python query service; a shared suite needs that route served "
        "beside Node (QA19-O1); harness-agent-deletions.test.ts checks its wire"
    ),
    "ArtifactReadAccess": (
        "its production class is an HTTP client of the Python query service's artifact read check; needs the "
        "cross-language rig (QA19-O1); harness-record-access.test.ts checks its wire"
    ),
    "RecordReadAccess": (
        "its production class is an HTTP client of the Python query service's record access route, whose graph side "
        "runs the record-access contract in Python (KNW-11); the Node suite needs the cross-language rig (QA19-O1)"
    ),
    "EngineSignInRelay": (
        "its production class is a Redis pub/sub client of the Python worker's relay, whose side runs "
        "SignInRelayContract (test_signin_relay.py); engine-signin-relay.test.ts checks its wire and channel names, "
        "a shared suite needs the cross-language rig (QA19-O1)"
    ),
    "ConfigDocumentStore": (
        "the control plane's configuration document store: CPD-1 brings its store and its contract suite"
    ),
}

# (port, class) -> why that implementing class is not run by the port's suite
NOT_UNDER_CONTRACT: dict[tuple[str, str], str] = {
    ("MembershipPort", "UnreadableDirectory"): (
        "CPD-9's fail-closed stand-in until the directory clients are composed (CPD-11): every read throws, which is "
        "the suite's MP-2 half and never its MP-1; RoleAuthorizer's AZV-3 runs over a membership that throws"
    ),
    ("AgentDirectory", "UnreadableDirectory"): "as for MembershipPort: every read throws (the suite's AD-2 half)",
}



def registry_overlays(roots: tuple[Path, ...]) -> tuple[dict[str, str], dict[str, str]]:
    """The ports and exemptions the other test roots' ``REGISTRY_OVERLAY`` files add."""
    ports: dict[str, str] = {}
    exempt: dict[str, str] = {}
    for root in roots:
        overlay = root / REGISTRY_OVERLAY
        if overlay.is_file():
            data = json.loads(overlay.read_text(encoding="utf-8"))
            ports.update(data.get("ports", {}))
            exempt.update(data.get("exempt", {}))
    return ports, exempt


_OVERLAY_PORTS, _OVERLAY_EXEMPT = registry_overlays(TEST_ROOTS[1:])
NODE_PORTS.update(_OVERLAY_PORTS)
NODE_EXEMPT.update(_OVERLAY_EXEMPT)

_CLASS = re.compile(
    r"export\s+class\s+(\w+)\s*(?:<[^{]*?>)?\s*(?:extends\s+[\w.]+(?:<[^{]*?>)?\s*)?implements\s+([^{]+)\{", re.S
)
_INTERFACE = re.compile(r"^export\s+interface\s+(\w+)", re.M)
_SUITE = re.compile(r"^export\s+function\s+(\w+Contract)\s*[<(]", re.M)
_IMPORT = re.compile(r"from\s+'(\.[^']+)'")


@dataclass(frozen=True)
class _Impl:
    cls: str
    path: Path


# Where the control plane keeps its in-memory adapters; only their ``InMemory*`` classes count from here (a test's
# probes and stand-ins implement ports too, without being adapters).
TEST_SUPPORT_ADAPTERS = NODE_APP / "tests" / "modules" / "control-plane" / "support"


def _sources() -> list[Path]:
    """Everywhere a port may be implemented: hosts adapt them outside the module (``enterprise_search/adapters``)."""
    return sorted((NODE_APP / "src").rglob("*.ts"))


def _interfaces_of(clause: str) -> list[str]:
    depth, names, current = 0, [], ""
    for ch in clause:
        depth += ch == "<"
        depth -= ch == ">"
        if ch == "," and depth == 0:
            names.append(current)
            current = ""
        else:
            current += ch
    names.append(current)
    return [m.group(1) for n in names if (m := re.match(r"\s*(\w+)", n))]


@cache
def _implementations() -> dict[str, tuple[_Impl, ...]]:
    found: dict[str, list[_Impl]] = {}
    support = sorted(TEST_SUPPORT_ADAPTERS.rglob("*.ts")) if TEST_SUPPORT_ADAPTERS.exists() else []
    for path in [*_sources(), *support]:
        in_support = path.is_relative_to(TEST_SUPPORT_ADAPTERS)
        for match in _CLASS.finditer(path.read_text(encoding="utf-8")):
            if in_support and not match.group(1).startswith("InMemory"):
                continue
            for interface in _interfaces_of(match.group(2)):
                found.setdefault(interface, []).append(_Impl(match.group(1), path))
    return {k: tuple(v) for k, v in found.items()}


@cache
def _declared_ports() -> frozenset[str]:
    return frozenset(
        name
        for m in MODULES
        if (NODE_APP / "src" / "modules" / m / "ports").exists()
        for path in (NODE_APP / "src" / "modules" / m / "ports").rglob("*.ts")
        for name in _INTERFACE.findall(path.read_text(encoding="utf-8"))
    )


def _discovered() -> set[str]:
    return {
        port
        for port in _declared_ports()
        if any(impl.cls.startswith("InMemory") for impl in _implementations().get(port, ()))
    }


@cache
def _suites() -> dict[str, Path]:
    """Each suite function -> the support file defining it."""
    return {
        name: path
        for root in TEST_ROOTS
        for path in root.rglob("*-contract.ts")
        for name in _SUITE.findall(path.read_text(encoding="utf-8"))
    }


@cache
def _suite_call() -> re.Pattern[str]:
    # One pass per text: a pattern per suite, compiled for every file, was most of this module's 12 s.
    names = sorted(_suites(), key=len, reverse=True)
    return re.compile(rf"\b({'|'.join(map(re.escape, names))})\s*[<(]")


def _calls(text: str) -> set[str]:
    return set(_suite_call().findall(text))


@cache
def _runs(suite: str) -> frozenset[str]:
    """``suite`` and every suite it calls."""
    seen, pending = set[str](), [suite]
    while pending:
        name = pending.pop()
        if name not in seen:
            seen.add(name)
            pending.extend(_calls(_suites()[name].read_text(encoding="utf-8")))
    return frozenset(seen)


def _with_imports(path: Path) -> str:
    """A test file and the support files it imports (where its stores are often built)."""
    text = path.read_text(encoding="utf-8")
    imported = [(path.parent / f"{rel}.ts").resolve() for rel in _IMPORT.findall(text)]
    return "\n".join([text, *(p.read_text(encoding="utf-8") for p in imported if p.is_file() and "support" in p.parts)])


@cache
def _test_files() -> dict[Path, tuple[frozenset[str], str]]:
    """Each test file -> the suites it runs and the text that may name its adapters."""
    files: dict[Path, tuple[frozenset[str], str]] = {}
    for path in (p for root in TEST_ROOTS for p in root.rglob("*.ts")):
        if not re.search(r"\.(i?test)\.ts$", path.name):
            continue
        direct = _calls(path.read_text(encoding="utf-8"))
        if direct:
            files[path] = (frozenset().union(*(_runs(s) for s in direct)), _with_imports(path))
    return files


def _runs_against(suite: str, cls: str) -> bool:
    return any(suite in suites and re.search(rf"\b{cls}\b", text) for suites, text in _test_files().values())


def test_every_node_port_with_an_in_memory_adapter_is_registered_or_exempt() -> None:
    discovered = _discovered()
    unregistered = sorted(discovered - NODE_PORTS.keys() - NODE_EXEMPT.keys())
    stale = sorted((NODE_PORTS.keys() | NODE_EXEMPT.keys()) - discovered)
    assert unregistered == [], f"Node ports with an in-memory adapter need a shared contract suite: {unregistered}"
    assert stale == [], f"no longer a port with an in-memory adapter; remove from NODE_PORTS/NODE_EXEMPT: {stale}"


@pytest.mark.parametrize("port", sorted(NODE_PORTS))
def test_the_suite_exists(port: str) -> None:
    assert NODE_PORTS[port] in _suites(), f"no <test root>/**/support/*-contract.ts exports {NODE_PORTS[port]}"


@pytest.mark.parametrize("port", sorted(NODE_PORTS))
def test_every_implementation_runs_its_ports_suite(port: str) -> None:
    suite = NODE_PORTS[port]
    outside = sorted(
        impl.cls
        for impl in _implementations().get(port, ())
        if (port, impl.cls) not in NOT_UNDER_CONTRACT and not _runs_against(suite, impl.cls)
    )
    assert outside == [], f"classes implementing {port} that no test runs {suite} against: {outside}"


def test_every_exemption_names_an_implementation() -> None:
    stale = sorted(
        (port, cls)
        for port, cls in NOT_UNDER_CONTRACT
        if port not in NODE_PORTS or cls not in {impl.cls for impl in _implementations().get(port, ())}
    )
    assert stale == [], f"remove from NOT_UNDER_CONTRACT: {stale}"


def test_the_scanner_reads_control_plane_test_support_adapters_only() -> None:
    """C2-2: in-memory adapters in the control plane's test support count; its probes and stand-ins do not."""
    transaction = {impl.cls for impl in _implementations()["ControlPlaneTransaction"]}
    assert "InMemoryControlPlaneTransaction" in transaction
    assert "ProbedTransaction" not in transaction
    assert {"MongoControlPlaneTransaction", "OrderedStepsTransaction"} <= transaction
    assert "StaticSnapshots" not in {impl.cls for impl in _implementations()["SnapshotSource"]}


def test_the_scanner_reads_multi_line_and_generic_implements_clauses() -> None:
    journal = {impl.cls for impl in _implementations()["JournalStore"]}
    assert {"InMemoryJournalStore", "MongoJournalStore", "SealedJournalStore"} <= journal
    assert {"ExecutionStore", "ChildRunStore"} <= {
        port for port, impls in _implementations().items() if any(i.cls == "InMemoryExecutionStore" for i in impls)
    }
    assert "describeJournalStoreContract" in _suites()


def test_r27_seam_other_test_roots_are_read_and_their_overlays_merged(tmp_path: Path) -> None:
    """An edition keeps its adapters' suites, their runs and its own registry entries in a test root of its own."""
    assert TEST_ROOTS[0] == NODE_APP / "tests"
    assert all(root.name == "tests" and root.parent.parent == NODE_APP for root in TEST_ROOTS[1:])
    one, two, bare = tmp_path / "a" / "tests", tmp_path / "b" / "tests", tmp_path / "c" / "tests"
    for root in (one, two, bare):
        root.mkdir(parents=True)
    _ = (one / REGISTRY_OVERLAY).write_text(json.dumps({"ports": {"P1": "describeP1Contract"}}), encoding="utf-8")
    _ = (two / REGISTRY_OVERLAY).write_text(json.dumps({"exempt": {"P2": "why"}}), encoding="utf-8")
    assert registry_overlays((one, two, bare)) == ({"P1": "describeP1Contract"}, {"P2": "why"})
    assert registry_overlays(()) == ({}, {})
