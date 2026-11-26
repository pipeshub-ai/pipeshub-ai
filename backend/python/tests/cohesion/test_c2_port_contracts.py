"""C-2: every control-layer port has an in-memory adapter and a contract suite a test runs, and so does every
production adapter of it (QA-19, CON-12).

ARCHITECTURE principle 9 (Loom): substitutability is tested, not assumed. A
port added to ``harness_core.{journal,effects,invoker,engines,sandbox,workspace,
interactions,access,gateway,artifacts,skills,credentials,context,knowledge,mcp_hub,memories,models}`` fails this test until it is registered here
with its adapter and contract, or exempted with a reason. A production class that implements a registered port
fails it until a test runs the port's contract against that class, or ``NOT_UNDER_CONTRACT`` says why not.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from functools import cache
from typing import TYPE_CHECKING

import pytest

from tests.cohesion.support import (
    CONTROL_LAYER,
    PYTHON_ROOT,
    dotted,
    is_protocol,
    modules,
    top_level_classes,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# Where production composes adapters from. The control-plane root is scanned once CPD lands it, for its adapters and
# (below) its ports, so CPD-1/CPD-2 ports join this registry rather than a parallel one.
PRODUCTION_ROOTS = ("harness_core", "harness_engines", "app/harness", "app/control_plane")
# Where contract suites live: the harness core's test support, and the control plane's for ports typed by its own
# generated contracts.
CONTRACT_PACKAGES = ("harness_core.testing.", "app.control_plane.testing.")
CONTROL_PLANE_PORT_ROOTS = ("app/control_plane",)

# port -> (in-memory adapter, contract suite)
PORTS: dict[str, tuple[str, str]] = {
    "harness_core.policy.ports.PolicyResolver": (
        "harness_core.policy.resolver.DimensionPolicyResolver",
        "harness_core.testing.contracts.policy_resolver.PolicyResolverContract",
    ),
    "harness_core.activity.ports.CommandActivity": (
        "harness_core.activity.stream.BoundedCommandActivity",
        "harness_core.testing.contracts.command_activity.CommandActivityContract",
    ),
    "harness_core.gateway.priority_gate.ProviderAdmission": (
        "harness_core.gateway.priority_gate.PriorityGate",
        "harness_core.testing.contracts.provider_admission.ProviderAdmissionContract",
    ),
    "harness_core.gateway.refusal_reports.RefusalSink": (
        "harness_core.gateway.refusal_reports.InMemoryRefusalSink",
        "harness_core.testing.contracts.refusal_sink.RefusalSinkContract",
    ),
    "harness_core.effects.receipts.ExecutionReceipts": (
        "harness_core.effects.receipts.InMemoryExecutionReceipts",
        "harness_core.testing.contracts.receipts.ExecutionReceiptsContract",
    ),
    "harness_core.effects.reconcile.Reconciler": (
        "harness_core.effects.reconcile.TimedReconciler",
        "harness_core.testing.contracts.reconcile.ReconcilerContract",
    ),
    "harness_core.effects.reconcile.ReconcileLookup": (
        "harness_core.effects.memory.InMemoryReconcileLookup",
        "harness_core.testing.contracts.reconcile.ReconcileLookupContract",
    ),
    "harness_core.access.ports.RunModelGrants": (
        "harness_core.access.memory.InMemoryRunModelGrants",
        "harness_core.testing.contracts.model_access.RunModelGrantsContract",
    ),
    "harness_core.access.ports.ModelAccessResolver": (
        "harness_core.access.memory.StaticModelAccessResolver",
        "harness_core.testing.contracts.model_access.ModelAccessResolverContract",
    ),
    "harness_core.access.ports.OrgAccessPolicies": (
        "harness_core.access.memory.InMemoryOrgAccessPolicies",
        "harness_core.testing.contracts.access_policies.OrgAccessPoliciesContract",
    ),
    "harness_core.budget.ports.BudgetAccounts": (
        "harness_core.budget.memory.InMemoryBudgetLedger",
        "harness_core.testing.contracts.budget.BudgetAccountsContract",
    ),
    "harness_core.budget.ports.BudgetLedger": (
        "harness_core.budget.memory.InMemoryBudgetLedger",
        "harness_core.testing.contracts.budget.BudgetLedgerContract",
    ),
    "harness_core.engines.usage.RunUsageLedger": (
        "harness_core.engines.usage.AccountUsageLedger",
        "harness_core.testing.contracts.usage_ledger.RunUsageLedgerContract",
    ),
    "harness_core.access.ports.ModelAccountStore": (
        "harness_core.access.accounts.InMemoryModelAccountStore",
        "harness_core.testing.contracts.model_accounts.ModelAccountStoreContract",
    ),
    "harness_core.access.ports.SpendCounter": (
        "harness_core.access.memory.InMemorySpendCounter",
        "harness_core.testing.contracts.model_access.SpendCounterContract",
    ),
    "harness_core.credentials.ports.CredentialService": (
        "harness_core.credentials.service.HandleCredentialService",
        "harness_core.testing.contracts.credentials.CredentialServiceContract",
    ),
    "harness_core.credentials.ports.SecretSource": (
        "harness_core.credentials.memory.InMemorySecretSource",
        "harness_core.testing.contracts.credentials.SecretSourceContract",
    ),
    "harness_core.journal.ports.JournalPort": (
        "harness_core.journal.memory.InMemoryJournal",
        "harness_core.testing.contracts.journal.JournalContract",
    ),
    "harness_core.journal.ports.LiveBusPort": (
        "harness_core.journal.memory.InMemoryLiveBus",
        "harness_core.testing.contracts.livebus.LiveBusContract",
    ),
    "harness_core.journal.ports.ChildRunPort": (
        "harness_core.journal.memory.InMemoryJournal",
        "harness_core.testing.contracts.children.ChildRunContract",
    ),
    "harness_core.journal.ports.ContentStorePort": (
        "harness_core.journal.memory.InMemoryContentStore",
        "harness_core.testing.contracts.content.ContentStoreContract",
    ),
    "harness_core.journal.ports.CheckpointPort": (
        "harness_core.journal.memory.InMemoryCheckpointStore",
        "harness_core.testing.contracts.content.CheckpointContract",
    ),
    "harness_core.effects.log.EffectLog": (
        "harness_core.effects.log.RequestEffectLog",
        "harness_core.testing.contracts.effect_log.EffectLogContract",
    ),
    "harness_core.invoker.ports.ToolCatalog": (
        "harness_core.invoker.memory.InMemoryToolCatalog",
        "harness_core.testing.contracts.tool_catalog.ToolCatalogContract",
    ),
    "harness_core.engines.ports.EngineAdapter": (
        "harness_core.testing.engines.fake_engine.FakeEngineAdapter",
        "harness_core.testing.engines.contract.EngineAdapterContract",
    ),
    "harness_core.engines.ports.EngineRegistry": (
        "harness_core.engines.registry.AllowlistEngineRegistry",
        "harness_core.testing.engines.contract.EngineRegistryContract",
    ),
    "harness_core.engines.ports.EngineCeilingSource": (
        "harness_core.engines.memory.InMemoryEngineCeilings",
        "harness_core.testing.contracts.engine_profiles.EngineCeilingSourceContract",
    ),
    "harness_core.engines.ports.EngineCheckpointStore": (
        "harness_core.engines.checkpoints.ObjectStoreEngineCheckpoints",
        "harness_core.testing.contracts.engine_controller.EngineCheckpointStoreContract",
    ),
    "harness_core.engines.binary.ImageBinaryReader": (
        "harness_core.testing.engines.binary.InMemoryImageBinaries",
        "harness_core.testing.contracts.image_binaries.ImageBinaryReaderContract",
    ),
    "harness_core.engines.ports.SandboxSnapshotStore": (
        "harness_core.engines.snapshots.ObjectStoreSandboxSnapshots",
        "harness_core.testing.contracts.sandbox_snapshots.SandboxSnapshotStoreContract",
    ),
    "harness_core.engines.ports.EngineEventSink": (
        "harness_core.engines.memory.InMemoryEngineEventSink",
        "harness_core.testing.contracts.engine_controller.EngineEventSinkContract",
    ),
    "harness_core.engines.ports.ToolGatewayHost": (
        "harness_core.testing.engines.tool_host.InMemoryToolGatewayHost",
        "harness_core.testing.contracts.engine_controller.ToolGatewayHostContract",
    ),
    "harness_core.engines.durable.ports.EngineRunPresenter": (
        "harness_core.engines.durable.ports.SilentPresenter",
        "harness_core.testing.contracts.engine_kernel.EngineRunPresenterContract",
    ),
    "harness_core.engines.durable.ports.EngineRuntimeAssembler": (
        "harness_core.testing.engines.durable.MemoryEngineAssembler",
        "harness_core.testing.contracts.engine_kernel.EngineRuntimeAssemblerContract",
    ),
    "harness_core.interactions.ports.InteractionBridge": (
        "harness_core.interactions.memory.InMemoryInteractionBridge",
        "harness_core.testing.contracts.interactions.InteractionBridgeContract",
    ),
    "harness_core.engines.signin.SignInRelay": (
        "harness_core.engines.signin_relay.InMemorySignInRelay",
        "harness_core.testing.contracts.signin_relay.SignInRelayContract",
    ),
    "harness_core.interactions.ports.InteractionWakeup": (
        "harness_core.interactions.memory.InMemoryInteractionWakeup",
        "harness_core.testing.contracts.interactions.InteractionWakeupContract",
    ),
    "harness_core.journal.ready.RunReadySignal": (
        "harness_core.journal.ready.InMemoryRunReadySignal",
        "harness_core.testing.contracts.run_ready.RunReadySignalContract",
    ),
    "harness_core.sandbox.ports.SandboxBroker": (
        "harness_core.testing.sandbox.fake.FakeSandboxBroker",
        "harness_core.testing.sandbox.contract.SandboxBrokerContract",
    ),
    "harness_core.sandbox.provider.ISandboxProvider": (
        "harness_core.sandbox.provider.BrokerSandboxProvider",
        "harness_core.testing.sandbox.provider_contract.SandboxProviderContract",
    ),
    "harness_core.sandbox.ports.WarmSandboxPool": (
        "harness_core.testing.sandbox.fake.FakeSandboxBroker",
        "harness_core.testing.sandbox.pool_contract.WarmPoolContract",
    ),
    "harness_core.sandbox.ports.SessionFolderEraser": (
        "harness_core.testing.sandbox.fake.FakeSandboxBroker",
        "harness_core.testing.sandbox.erase_contract.SessionFolderEraserContract",
    ),
    "harness_core.audit.ports.RunEvidenceSource": (
        "harness_core.audit.memory.InMemoryRunEvidenceSource",
        "harness_core.testing.contracts.audit.RunEvidenceSourceContract",
    ),
    "harness_core.telemetry.ports.HarnessTracer": (
        "harness_core.telemetry.memory.RecordingTracer",
        "harness_core.testing.contracts.tracer.HarnessTracerContract",
    ),
    "harness_core.sandbox.metering.BrokerMetrics": (
        "harness_core.sandbox.metering.RecordingBrokerMetrics",
        "harness_core.testing.contracts.broker_metrics.BrokerMetricsContract",
    ),
    "harness_core.telemetry.metrics.GatewayMetrics": (
        "harness_core.telemetry.metrics.RecordingGatewayMetrics",
        "harness_core.testing.contracts.gateway_metrics.GatewayMetricsContract",
    ),
    "harness_core.telemetry.metrics.ReadCacheMetrics": (
        "harness_core.telemetry.metrics.RecordingReadCacheMetrics",
        "harness_core.testing.contracts.gateway_metrics.ReadCacheMetricsContract",
    ),
    "harness_core.mcp_hub.ports.ToolSchemaCache": (
        "harness_core.mcp_hub.memory.InMemoryToolSchemaCache",
        "harness_core.testing.contracts.mcp_hub.ToolSchemaCacheContract",
    ),
    "harness_core.sandbox.mcp_runner.ports.MCPRunner": (
        "harness_core.sandbox.mcp_runner.memory.InMemoryMCPRunner",
        "harness_core.testing.sandbox.mcp_runner.MCPRunnerContract",
    ),
    "harness_core.sandbox.egress.ports.EgressBindings": (
        "harness_core.sandbox.egress.bindings.InMemoryEgressBindings",
        "harness_core.testing.sandbox.egress_contracts.EgressBindingsContract",
    ),
    "harness_core.sandbox.egress.ports.EgressRevocations": (
        "harness_core.sandbox.egress.bindings.InMemoryEgressBindings",
        "harness_core.testing.sandbox.egress_contracts.EgressRevocationsContract",
    ),
    "harness_core.sandbox.egress.ports.TokenSource": (
        "harness_core.sandbox.egress.tokens.InMemoryTokenSource",
        "harness_core.testing.sandbox.egress_contracts.TokenSourceContract",
    ),
    "harness_core.sandbox.egress.ports.HostResolver": (
        "harness_core.sandbox.egress.resolver.StaticResolver",
        "harness_core.testing.sandbox.egress_contracts.HostResolverContract",
    ),
    "harness_core.sandbox.egress.ports.EgressStateStore": (
        "harness_core.sandbox.egress.state.InMemoryEgressStateStore",
        "harness_core.testing.sandbox.egress_contracts.EgressStateStoreContract",
    ),
    "harness_core.workspace.ports.BinaryObjectStore": (
        "harness_core.workspace.memory.InMemoryBinaryObjectStore",
        "harness_core.testing.contracts.workspace.BinaryObjectStoreContract",
    ),
    "harness_core.workspace.ports.WorkspaceStore": (
        "harness_core.workspace.memory.InMemoryWorkspaceStore",
        "harness_core.testing.contracts.workspace.WorkspaceStoreContract",
    ),
    "harness_core.workspace.ports.WorkspaceSync": (
        "harness_core.workspace.sync.tar_sync.TarWorkspaceSync",
        "harness_core.testing.contracts.workspace_sync.WorkspaceSyncContract",
    ),
    "harness_core.workspace.ports.KeyProvider": (
        "harness_core.workspace.memory.InMemoryKeyProvider",
        "harness_core.testing.contracts.workspace.KeyProviderContract",
    ),
    "harness_core.workspace.ports.WorkspaceWriters": (
        "harness_core.workspace.memory.InMemoryWorkspaceWriters",
        "harness_core.testing.contracts.workspace_forks.WorkspaceWritersContract",
    ),
    "harness_core.workspace.ports.ChildWorkspaces": (
        "harness_core.workspace.forks.StoreChildWorkspaces",
        "harness_core.testing.contracts.workspace_forks.ChildWorkspacesContract",
    ),
    "harness_core.engines.durable.ports.EngineInputSource": (
        "harness_core.engines.durable.inputs.StoreInputSource",
        "harness_core.testing.contracts.engine_inputs.EngineInputSourceContract",
    ),
    "harness_core.context.ports.CompactionStrategy": (
        "harness_core.context.compaction.TruncationCompaction",
        "harness_core.testing.contracts.compaction.CompactionStrategyContract",
    ),
    "harness_core.models.prompts.PromptRegistry": (
        "harness_core.models.prompts.InMemoryPromptRegistry",
        "harness_core.testing.contracts.prompts.PromptRegistryContract",
    ),
    "harness_core.memories.ports.MemorySource": (
        "harness_core.memories.memory.InMemoryMemorySource",
        "harness_core.testing.contracts.memories.MemorySourceContract",
    ),
    "harness_core.security.labels.SourceLedger": (
        "harness_core.engines.durable.ledger.ItemLabelLedger",
        "harness_core.testing.contracts.source_ledger.SourceLedgerContract",
    ),
    "harness_core.artifacts.ports.ArtifactPublisher": (
        "harness_core.artifacts.publisher.WorkspaceArtifactPublisher",
        "harness_core.testing.contracts.artifacts.ArtifactPublisherContract",
    ),
    "harness_core.artifacts.ports.ArtifactStore": (
        "harness_core.artifacts.memory.InMemoryArtifactStore",
        "harness_core.testing.contracts.artifacts.ArtifactStoreContract",
    ),
    "harness_core.artifacts.ports.PublicationLog": (
        "harness_core.artifacts.memory.InMemoryPublicationLog",
        "harness_core.testing.contracts.artifacts.PublicationLogContract",
    ),
    "harness_core.skills.ports.SkillContentSource": (
        "harness_core.skills.memory.InMemorySkillContentSource",
        "harness_core.testing.contracts.skills.SkillContentSourceContract",
    ),
    "harness_core.skills.ports.SkillBundleBuilder": (
        "harness_core.skills.builder.StoredSkillBundleBuilder",
        "harness_core.testing.contracts.skills.SkillBundleBuilderContract",
    ),
    "harness_core.skills.ports.SkillMaterializer": (
        "harness_core.skills.materializer.TarSkillMaterializer",
        "harness_core.testing.contracts.skills.SkillMaterializerContract",
    ),
    "harness_core.gateway.ports.ToolEndpoint": (
        "harness_core.gateway.endpoint.RunToolEndpoint",
        "harness_core.testing.contracts.tool_endpoint.ToolEndpointContract",
    ),
    "harness_core.gateway.ports.CitationSink": (
        "harness_core.gateway.memory.InMemoryCitationSink",
        "harness_core.testing.contracts.gateway.CitationSinkContract",
    ),
    "harness_core.gateway.ports.GatewayKeySource": (
        "harness_core.gateway.memory.StaticGatewayKeySource",
        "harness_core.testing.contracts.gateway.GatewayKeySourceContract",
    ),
    "harness_core.kernel.durable.ports.DurableModelPort": (
        "harness_core.testing.durable.ScriptedModelPort",
        "harness_core.testing.contracts.durable_ports.DurableModelPortContract",
    ),
    "harness_core.kernel.durable.ports.DurableToolPort": (
        "harness_core.testing.durable.FunctionToolPort",
        "harness_core.testing.contracts.durable_ports.DurableToolPortContract",
    ),
    "harness_core.kernel.durable.ports.PreRunStep": (
        "harness_core.testing.durable_ports.StaticPreRunStep",
        "harness_core.testing.contracts.durable_ports.PreRunStepContract",
    ),
    "harness_core.kernel.durable.ports.AfterRunStep": (
        "harness_core.testing.durable_ports.LedgerAfterRunStep",
        "harness_core.testing.contracts.after_run.AfterRunStepContract",
    ),
    "harness_core.kernel.durable.ports.PostRunStep": (
        "harness_core.testing.durable_ports.StaticPostRunStep",
        "harness_core.testing.contracts.durable_ports.PostRunStepContract",
    ),
    "harness_core.kernel.durable.ports.PreRunGate": (
        "harness_core.testing.durable_ports.SlotPreRunGate",
        "harness_core.testing.contracts.durable_ports.PreRunGateContract",
    ),
    "harness_core.kernel.durable.ports.CapabilityCheck": (
        "harness_core.testing.durable_ports.StaticCapabilityCheck",
        "harness_core.testing.contracts.durable_ports.CapabilityCheckContract",
    ),
    "harness_core.kernel.durable.ports.RunPresenter": (
        "harness_core.testing.durable_ports.SilentRunPresenter",
        "harness_core.testing.contracts.durable_ports.RunPresenterContract",
    ),
    "harness_core.kernel.durable.ports.RunRestore": (
        "harness_core.testing.durable_ports.SlotRunRestore",
        "harness_core.testing.contracts.durable_ports.RunRestoreContract",
    ),
    "harness_core.kernel.durable.ports.InputRenderer": (
        "harness_core.testing.durable_ports.PlainInputRenderer",
        "harness_core.testing.contracts.durable_ports.InputRendererContract",
    ),
    "harness_core.kernel.durable.ports.ImportedHistory": (
        "harness_core.kernel.durable.transcript.PlainImportedHistory",
        "harness_core.testing.contracts.durable_ports.ImportedHistoryContract",
    ),
    "harness_core.kernel.durable.ports.TurnShaper": (
        "harness_core.testing.durable_ports.PassThroughTurnShaper",
        "harness_core.testing.contracts.durable_turns.TurnShaperContract",
    ),
    "harness_core.engines.durable.ports.EngineWrapUp": (
        "harness_core.engines.durable.wrap_up.ModelEngineWrapUp",
        "harness_core.testing.contracts.engine_wrap_up.EngineWrapUpContract",
    ),
    "harness_core.kernel.durable.ports.WrapUpPort": (
        "harness_core.budget.wrap_up.WrapUpReserve",
        "harness_core.testing.contracts.wrap_up.WrapUpContract",
    ),
    "harness_core.kernel.durable.ports.ResponseReview": (
        "harness_core.testing.durable_ports.AcceptingResponseReview",
        "harness_core.testing.contracts.durable_turns.ResponseReviewContract",
    ),
    "harness_core.kernel.durable.ports.ChildResultShaper": (
        "harness_core.testing.durable_ports.IdentityChildResultShaper",
        "harness_core.testing.contracts.durable_turns.ChildResultShaperContract",
    ),
    "harness_core.kernel.durable.ports.LoopDirector": (
        "harness_core.testing.durable_ports.IdleLoopDirector",
        "harness_core.testing.contracts.durable_turns.LoopDirectorContract",
    ),
    "harness_core.kernel.durable.context_compiler.ContextHost": (
        "harness_core.testing.durable_ports.DefaultContextHost",
        "harness_core.testing.contracts.durable_turns.ContextHostContract",
    ),
    "harness_core.kernel.durable.delegation.DelegationTools": (
        "harness_core.kernel.durable.delegation.NamedDelegation",
        "harness_core.testing.contracts.durable_turns.DelegationToolsContract",
    ),
    "harness_core.kernel.durable.delegation.ChildBudget": (
        "harness_core.kernel.durable.delegation.InterimChildBudget",
        "harness_core.testing.contracts.durable_turns.ChildBudgetContract",
    ),
    "harness_core.kernel.durable.routing.RoutePorts": (
        "harness_core.testing.durable_ports.StaticRoutePorts",
        "harness_core.testing.contracts.durable_turns.RoutePortsContract",
    ),
    "harness_core.knowledge.ports.PublicationDirectory": (
        "harness_core.knowledge.memory.InMemoryPublicationDirectory",
        "harness_core.testing.contracts.publications.PublicationDirectoryContract",
    ),
    "harness_core.security.secrets.CallSecrets": (
        "harness_core.security.secrets.ResolvedSecrets",
        "harness_core.testing.contracts.call_secrets.CallSecretsContract",
    ),
    # The control plane's Python runtime ports (03-interfaces §3.2, §4.5, §5.2, §5.4; CP-SKEL). Their types are the
    # control plane's generated contracts, which harness_core may not import, so adapters and suites are the control
    # plane's own test support.
    "app.control_plane.runtime.ports.ItemRuntime": (
        "app.control_plane.testing.memory.InMemoryItemRuntime",
        "app.control_plane.testing.contracts.ItemRuntimeContract",
    ),
    "app.control_plane.runtime.ports.ItemRuntimeRegistry": (
        "app.control_plane.testing.memory.InMemoryItemRuntimeRegistry",
        "app.control_plane.testing.contracts.ItemRuntimeRegistryContract",
    ),
    "app.control_plane.runtime.ports.CredentialBroker": (
        "app.control_plane.testing.memory.InMemoryCredentialBroker",
        "app.control_plane.testing.contracts.CredentialBrokerContract",
    ),
    "app.control_plane.runtime.ports.ChangeNotifier": (
        "app.control_plane.testing.memory.InMemoryChangeNotifier",
        "app.control_plane.testing.contracts.ChangeNotifierContract",
    ),
    "app.control_plane.runtime.ports.LiveLayerCache": (
        "app.control_plane.testing.memory.InMemoryLiveLayerCache",
        "app.control_plane.testing.contracts.LiveLayerCacheContract",
    ),
    "app.control_plane.runtime.ports.RunConfigPinner": (
        "app.control_plane.testing.pinning.InMemoryRunConfigPinner",
        "app.control_plane.testing.pinning.RunConfigPinnerContract",
    ),
}

EXEMPT: dict[str, str] = {
    "harness_core.telemetry.security_signals.SecuritySignalSink": (
        "a counting sink with a no-op default, not a store (ADR-OPS-03): app.telemetry.modules.security_metrics is its "
        "Prometheus adapter; test_sec_d_13_security_signals.py drives the helpers through a recording fake, a sink "
        "that throws and the Prometheus adapter itself"
    ),
    "harness_core.sandbox.ports.ReportsMemoryExhaustion": (
        "an optional capability of a SandboxHandle (R31GVISO-FU-3), not a store: the Docker, remote and fake handles "
        "answer it; test_r32_remote_memory.py runs the remote route against the fake, test_r32_out_of_memory.py the "
        "controller and kernel, and SEC-ISO-06's memory balloon the Docker broker on runc and gVisor"
    ),
    "harness_core.sandbox.ports.HoldsSandboxFlow": (
        "an optional capability of a SandboxHandle (R38EGRESS-FU-1), not a store: the Docker and remote handles answer "
        "it, a handle without it reads as \"cannot\"; test_r38egress_fu1_flow_hold_survives_restart.py runs the remote "
        "route against the fake broker, an older broker, and the Docker egress wiring through a proxy restart"
    ),
    "harness_core.sandbox.ports.ClosesClients": (
        "an optional capability of a SandboxBroker (R46LANE-FU-3), not a store: the remote broker closes its HTTP "
        "session, the failover broker and the provider close what they wrap; test_r46lane_fu3_worker_closes_sessions.py "
        "drives the worker's stop over the remote, failover and provider brokers and checks every session is closed"
    ),
    "harness_core.engines.environments.EnvironmentSnapshotter": (
        "an extension point with no adapter in this build (C20.18): without one an environment's setup runs in every "
        "session; test_wp91_environments.py drives prepare_environment's restore and keep calls through a fake"
    ),
    "harness_core.engines.cache_mounts.SharedCacheMounts": (
        "an extension point with no adapter in this build: without one a run mounts no shared cache; "
        "test_cache_mounts.py drives the controller's mount, verify and refusal through a fake"
    ),
    "harness_core.models.provider_receipt.UsageLookup": (
        "a capability a provider transport may offer (WP-66), no store: what the provider billed for a response it "
        "kept; no production transport offers it while every one sends store false. test_wp66_honest_meter.py "
        "drives the gateway's one use of it through StoringTransport, a fake provider"
    ),
    "harness_core.context.quarantine.SummaryModel": (
        "the one model call the quarantined summarizer makes (G-58), no store: TransportModel over the run's metered "
        "transport implements it; test_ga28_quarantined_summarizer.py drives it with recorded transports and "
        "test_ga28_knowledge_summarize.py through the knowledge tool"
    ),
    "harness_core.gateway.priority_gate.AdmissionSlot": (
        "the slot a ProviderAdmission hands back, no store (G-45): GateSlot and the shared gate's SharedSlot; "
        "ProviderAdmissionContract covers its release and waited through each admission"
    ),
    "harness_core.sandbox.egress.volume.VolumeMetrics": (
        "a write-only metrics sink with nothing to read back (C6.12): NoVolumeMetrics and PipesHub's "
        "PrometheusVolumeMetrics implement it; test_wp45_egress_volume.py drives the flush through a recording fake "
        "and test_wp45_egress_metrics.py the Prometheus adapter"
    ),
    "harness_core.interactions.connect.ConnectGate": (
        "what the attempt's tools say of a call, no store (G-12): PipesHub's CatalogConnectGate implements it from the "
        "assembled catalog; test_ga07_connect_gate.py drives it through the assembler, test_ga07_connect_pause.py and "
        "test_ga07_connect_wait.py drive the kernel and the gateway with a fake"
    ),
    "harness_core.engines.git_checkout.GitCredentials": (
        "the credential source a GitCheckout carries (GIT-FLOW), no store: SealedGitCredentials and PipesHub's "
        "ToolsetGitCredentials implement it; test_gitflow_git.py drives the sealed one through the proxy and "
        "test_gitflow_pull_request.py the toolset one"
    ),
    "harness_core.engines.durable.ports.KeepsNative": (
        "an optional EngineRunPresenter facet (C2.39, C2.40): PipesHubEngineRunPresenter and the test presenter "
        "implement it; test_wp03_kernel.py EV-13 and UP-07 cover it through the kernel's sink"
    ),
    "harness_core.engines.native.RecordsNative": (
        "an optional EngineSession facet (C2.39): every adapter's session and the literal-text wrapper implement it; "
        "test_wp03_native_adapters.py UP-07 covers each adapter, test_wp03_kernel.py the controller"
    ),
    "harness_core.budget.metered.ReservesAhead": (
        "an optional LLMTransport facet, no store (WP-55): MeteredTransport implements it, and so may a model "
        "guard's transport; test_ctl5_followups_with_the_harness.py drives the follow-ups' reservation through it"
    ),
    "harness_core.engines.native.Taped": (
        "the shape the recorded decorator needs of a mapper, not a port: each adapter's mapper has a tape attribute"
    ),
    "harness_core.engines.ports.NativeTranscriptSink": (
        "an optional EngineEventSink facet (C2.39): JournalEngineEventSink implements it; test_wp03_kernel.py UP-07 "
        "covers it"
    ),
    "harness_core.engines.ports.ToolListings": (
        "what the Tool Gateway served one attempt, no store (E2E3-F6, WP-59): the controller's per-attempt endpoint "
        "counts it; test_e2e3_f6_controller_witness.py covers the count and the D lane the lab gateway's"
    ),
    "harness_core.engines.ports.RestartsFromSummary": (
        "an optional EngineSession facet (MG-10, C3.67, WP-91): AcpSession implements it; "
        "test_wp91_overflow_restart.py covers the overflow chain's use, test_wp91_acp_compaction.py and the fake "
        "agent's cf21 the session's restart"
    ),
    "harness_core.engines.ports.WitnessesToolGateway": (
        "an optional EngineSession facet (E2E3-F6, WP-59): AcpSession implements it; test_e2e3_f6_controller_witness.py "
        "covers the controller's hand-off, test_wp59_gateway_entry_and_witness.py and the D lane the session's check"
    ),
    "harness_core.gateway.ports.GrantedTools": (
        "an optional ToolEndpoint facet (the whole grant, for an engine's /pipeshub tool index): RunToolEndpoint and "
        "the kernel's tracking endpoint implement it; test_folder.py PF-4 and PF-9 cover it, lazy disclosure included"
    ),
    "harness_core.gateway.ports.NotesConnects": (
        "an optional ToolEndpoint facet, no store (R30ENG-FU-3): the controller's per-attempt endpoint implements it "
        "for the handshake witness; test_r30eng_fu3_handshake_witness.py covers the transport's notes and "
        "test_e2e3_f6_controller_witness.py the count"
    ),
    "harness_core.gateway.ports.NamesUntrusted": (
        "an optional CitationSink facet (KN-08): JournalCitationSink implements it; test_wp12_knowledge_service.py "
        "KN-08 covers it through RunToolEndpoint"
    ),
    "harness_core.knowledge.bulk_export.RecordPages": (
        "a callable per export (G-35); its one implementation, ListingPages, runs over the Knowledge Hub stub "
        "(test_wp40_knowledge_export_wiring.py BW-1)"
    ),
    "harness_core.knowledge.bulk_export.ExportShelf": (
        "where an export writes its file; its one implementation, ShelfRelay, is tested with and without a turn "
        "attached (test_ga19_bulk_export.py BE-2)"
    ),
    "harness_core.knowledge.bulk_export.ShelvesFiles": (
        "an optional ToolEndpoint facet like ShelvesResults: the controller's endpoint implements it over "
        "ToolFolder.shelve, which test_wp12_knowledge_shelf.py covers"
    ),
    "harness_core.gateway.result_cap.CapsResults": (
        "an optional ToolEndpoint facet (C4.10 toolResults.maxChars): the controller's endpoint carries the run's "
        "cap; test_c4_10_run_tuning.py C410-T2/T3 cover it with and without the facet"
    ),
    "harness_core.gateway.result_cap.ShelvesResults": (
        "an optional ToolEndpoint facet (KN-06): the controller's endpoint implements it; "
        "test_wp12_knowledge_shelf.py covers it over the gateway and in a controller turn"
    ),
    "harness_core.security.labels.ReadsUntrusted": (
        "an optional SourceLedger facet (KN-08): ItemLabelLedger, SessionSourceLedger and GrantingLedger implement "
        "it; test_wp12_rule_of_two.py and test_wp12_knowledge_service.py KN-08 cover it"
    ),
    "harness_core.journal.ports.SealingContentStore": (
        "an optional ContentStorePort facet (CHILDREF): InMemoryContentStore and PipesHubContentStore implement it; "
        "test_childref.py CHILDREF-4 and CHILDREF-5 cover it through both kernels and the store"
    ),
    "harness_core.kernel.durable.ports.TerminalTools": (
        "an optional DurableToolPort facet; DurableToolPortContract DP2.4 covers it on every adapter implementing it"
    ),
    "harness_core.kernel.durable.ports.WithheldTools": (
        "an optional DurableToolPort facet (ADR-RUN-04): RegistryToolPort implements it; "
        "test_qafinal_pw113_sub_agent_tools.py PW113-4 and PW113-5 cover it through the kernel and the session lists"
    ),
    "harness_core.kernel.durable.ports.TurnHook": (
        "a callable type (``__call__`` only) built by ``scope_turn_hook`` from a RunScope, not a host seam"
    ),
    "app.control_plane.catalog.ports.ReleaseItemSource": (
        "CPD-2's release-data source (C-2's control-plane hook found it): its in-memory adapter and contract come with "
        "the control-plane suites (QA-16); register it here then"
    ),
    "harness_core.models.base.SupportsComplete": (
        "a capability slice of Model (interface segregation), not a host seam: TransportModel and every transport implement it"
    ),
    "harness_core.models.base.SupportsStructuredComplete": "a capability slice of Model, as SupportsComplete",
    "harness_core.models.base.SupportsStreaming": "a capability slice of Model, as SupportsComplete",
    "harness_core.sandbox.egress.tunnel.DataPlaneFront": (
        "an asyncio transport protocol (the data-plane listener with tunnels on), not a typing.Protocol port; "
        "driven over loopback by CT-8..CT-12"
    ),
    "harness_core.telemetry.ports.SpanHandle": "the span a HarnessTracer returns; HarnessTracerContract drives it through every tracer",
    "harness_core.gateway.wire.GatewayWire": (
        "a pure wire codec, not a host seam: ChatWire and ResponsesWire are its two implementations, driven by the "
        "CW and RW table tests and the gateway suites (E3, E3b)"
    ),
    "harness_core.gateway.wire.WireRequest": "the parsed request a GatewayWire returns (ChatRequest, ResponsesRequest)",
    "harness_core.gateway.wire.StreamEncoder": "obtained only from GatewayWire.encoder (ChunkEncoder, ResponseEncoder)",
    "harness_core.engines.durable.ports.ShowsCommands": (
        "an optional EngineRunPresenter facet showing shell commands on the activity bus; PipesHubEngineRunPresenter"
        " implements it (UX5 EG-1, EG-2)"
    ),
    "harness_core.engines.durable.ports.ShowsToolResults": (
        "an optional EngineRunPresenter facet showing what a gateway call returned on its engine step;"
        " PipesHubEngineRunPresenter and the test world's RecordingPresenter implement it (CTRESULTS)"
    ),
    "harness_core.activity.ports.CommandRecorder": "made only by CommandActivity.begin; CommandActivityContract covers it",
    "harness_core.journal.ports.LiveSinkPort": "the publish half of LiveBusPort; LiveBusContract covers it",
    "harness_core.engines.ports.EngineSession": "obtained only from EngineAdapter.open; EngineAdapterContract drives it",
    "harness_core.engines.signin.SignInAttempt": "obtained only from SignInRelay.attempt; SignInRelayContract drives it",
    "harness_core.engines.signin.SignsIn": (
        "an optional EngineAdapter facet for an engine's own login (SIGNIN); ClaudeCodeAdapter implements it, driven "
        "by test_signin.py (SD-1..SD-8) and, on the real binary, test_signin_adapter_live.py (SL-1)"
    ),
    "harness_core.sandbox.ports.SandboxHandle": "obtained only from SandboxBroker.acquire; SandboxBrokerContract drives it",
    "harness_core.sandbox.ports.ProcessHandle": "obtained only from SandboxHandle.exec; SandboxBrokerContract drives it",
    "harness_core.sandbox.ports.ByteStream": "obtained only from SandboxHandle.connect; SandboxBrokerContract drives it",
    "harness_core.sandbox.pool.WarmBackend": (
        "the adapter side of WarmPool, implemented by each broker; WarmPoolContract drives it through the broker"
        " (fake and Docker) and WS-5 through a table backend"
    ),
    "harness_core.sandbox.mcp_runner.table.RunnerBackend": (
        "the adapter side of RunnerTable (a started runner); MCPRunnerContract drives it through every MCPRunner"
        " adapter (in-memory, the fake broker, Docker)"
    ),
    "harness_core.workspace.ports.KeyShredder": "the destroy half of KeyProvider; KeyProviderContract (SK-4) covers it",
    "harness_core.workspace.ports.WorkspaceManifestStore": (
        "superseded by WorkspaceStore (P3.2); its last consumer, VirtualFileSystem, moves in P3.3"
    ),
    "harness_core.sandbox.auth.BrokerAuth": (
        "the broker's client credential seam; StaticBearerAuth is the in-memory adapter, RB-3 and RB-7 test it"
    ),
    "harness_core.sandbox.auth.BrokerAuthVerifier": (
        "the broker's server credential seam; StaticBearerAuth is the in-memory adapter, RB-3 and RB-7 test it"
    ),
    "harness_core.effects.runner.ApprovalHold": (
        "a decision callback; BridgeApprovalHold implements it over any InteractionBridge (AH-1..AH-6 test it)"
    ),
    "harness_core.effects.runner.NamedHold": "an optional ApprovalHold facet naming the interaction; AH-3/AH-7 test it",
    "harness_core.effects.runner.ReasonedHold": (
        "an optional ApprovalHold facet reading a denial's reason; BridgeApprovalHold, BoundedHold and the engine"
        " kernel's hold implement it (WP-10 AP-05, test_wp10_gateway_hitl.py, test_wp10_engine_hitl.py)"
    ),
    "harness_core.effects.runner.EditingHold": (
        "an optional ApprovalHold facet reading the arguments a person approved instead; BridgeApprovalHold,"
        " BoundedHold and the engine kernel's hold implement it (WP-29 AP-06, test_wp29_gateway_edits.py, and"
        " test_wp29_edits.py fails any hold that forwards a denial's reason without it)"
    ),
    "harness_core.effects.edits.EditsApproved": (
        "an optional InteractionBridge/InteractionView facet: the edit a person approved a call with;"
        " InMemoryInteractionBridge, JournalInteractionBridge and ClaimInteractionView implement it (WP-29 AP-06)"
    ),
    "harness_core.interactions.ports.SaysWhy": (
        "an optional InteractionBridge/InteractionView facet: what a person said with a decision;"
        " InMemoryInteractionBridge, JournalInteractionBridge and ClaimInteractionView implement it (WP-10 AP-05)"
    ),
    "harness_core.engines.durable.ports.ShowsTimePlan": (
        "an optional EngineRunPresenter facet showing a turn's time plan (ADR-RUN-01 D4); PipesHubEngineRunPresenter"
        " implements it (test_wp99_time_plan_presenter.py WP99-UI)"
    ),
    "harness_core.engines.ports.EngineSuccessors": (
        "an optional EngineRegistry facet (ADR-OPS-01 D6): AllowlistEngineRegistry implements it;"
        " test_ops13_engine_successor.py and test_wp99_budget_kernel.py WP99-SUCC drive it through the kernel"
    ),
    "harness_core.engines.durable.ports.LinksFiles": (
        "an optional EngineRunPresenter facet linking the engine's final text to the files it published (MR-18);"
        " PipesHubEngineRunPresenter implements it (test_r50jfix_mr18_engine_links.py drives it through the kernel)"
    ),
    "harness_core.engines.durable.ports.AsksQuestions": (
        "an optional EngineRunPresenter facet showing an engine's ask_user question and its needs_input ending;"
        " PipesHubEngineRunPresenter and RecordingPresenter implement it (WP-10 AP-13, test_wp10_engine_question.py)"
    ),
    "harness_core.interactions.journal.InteractionView": (
        "the journal seam of JournalInteractionBridge; InteractionBridgeContract runs that bridge over"
        " ClaimInteractionView (in-memory and Node journals) and KH-* over the kernel's view"
    ),
    "harness_core.effects.types.ApprovalPolicy": "a pure decision function, tested where each policy is defined",
    "harness_core.effects.types.ExplainsDenial": (
        "an optional ApprovalPolicy facet naming a deny's rule; FlowGuardedPolicy implements it (SEC1 FL-4)"
    ),
    "harness_core.effects.types.RefreshingPolicy": (
        "an optional ApprovalPolicy facet read before a decision; LiveNarrowedApprovals implements it and the flow"
        " guard and the default-ask policy forward it (CPD-6 NTF-06, NTF-07)"
    ),
    "harness_core.effects.types.ConsultingPolicy": (
        "an optional ApprovalPolicy facet asked about one call before its decision; an edition's outside policy"
        " implements it, EffectRunner calls it (test_external_policy_gate.py XP-3)"
    ),
    "harness_core.effects.types.ExplainsAsk": (
        "an optional ApprovalPolicy facet saying why it asks; FlowGuardedPolicy implements it (UX1 AR-1..AR-6)"
    ),
    "harness_core.effects.types.GrantAnswers": (
        "an optional ApprovalPolicy facet: its ask is a default a conversation grant answers; ToolRulesPolicy"
        " implements it and the flow guard and the default-ask policy forward it (SYNC4 PW-094)"
    ),
    "harness_core.effects.types.LiftsDefault": (
        "an optional ApprovalPolicy facet: the call's own specific Allow lifts a host's class default; ToolRulesPolicy"
        " implements it, the flow guard forwards it and the default-ask policy reads it (D-ALLOW-1,"
        " test_allow1_default_lift.py)"
    ),
    "harness_core.security.labels.DescribesSources": (
        "an optional SourceLedger facet naming sources by title; SessionSourceLedger and GrantingLedger"
        " implement it (UX1 AR-3)"
    ),
    "harness_core.effects.runner.ExplainingHold": (
        "an optional ApprovalHold facet journaling why the policy asks with the request; the engine kernel's hold"
        " implements it (UX2 EA-1, EA-2)"
    ),
    "harness_core.effects.flow.VouchesAddress": (
        "a FlowGuardedPolicy input, not a store: whether a page fetch's address is one the run was given;"
        " app.web.addresses.RunAddresses implements it over the run's tool state and session pages, and a missing or"
        " failing one vouches for nothing (test_web_addresses.py)"
    ),
    "harness_core.effects.flow.GrantsContext": (
        "an optional SourceLedger facet giving the conversation grants a run holds to the child it asks for;"
        " GrantingLedger implements it (FLOWASK, test_flowask.py)"
    ),
    "harness_core.security.classification.ClassifiedSources": (
        "an optional SourceLedger facet (OWD-15, WP-92): what a conversation's classification ranks, live items"
        " included, and a child's inherited level; SessionSourceLedger implements it, reached through GrantingLedger"
        " by the classification port"
    ),
    "harness_core.security.classification.Classification": (
        "an opaque value, not a port: a run carries the classification its ClassificationPort made and reads only"
        " whether it is configured; the port's own default (NoClassification) is tested in"
        " test_classification_ports.py"
    ),
    "harness_core.security.labels.LabelsContext": (
        "an optional SourceLedger facet giving a run's label to the child it asks for; SessionSourceLedger and"
        " GrantingLedger implement it (UX2 VC-1)"
    ),
    "harness_core.effects.redaction.RedactsResults": (
        "an optional ApprovalPolicy facet naming its allow_with_redaction redaction; a classification approval layer"
        " may implement it and every policy wrapper forwards it (WP-22 PE-3)"
    ),
    "harness_core.engines.durable.tool_pin.PinsToolList": (
        "an optional ToolEndpoint facet holding tools/list to the session's list; RunToolEndpoint implements it and"
        " EngineToolPin drives it (WP-22 TP-1..TP-5)"
    ),
    "harness_core.access.ports.ConversationClassification": (
        "a pure decision over an org's classification and a model's data handling, run through the model gateway"
        " (WP-22 GA-16); ClassificationPort.gateway supplies it, none by default"
    ),
}


# (port, production class) -> why the class is not run by the port's contract. Only for a class that matches the
# port's shape without being its adapter, or one whose contract run is impossible here; never "no time yet".
NOT_UNDER_CONTRACT: dict[tuple[str, str], str] = {
    ("harness_core.workspace.ports.KeyProvider", "app.harness.workspace.node_store.LeasedKeys"): (
        "the base each attempt-leased key extends, with no route of its own (``_suffix`` is a subclass's); "
        "NodeKeyProvider runs KeyProviderContract"
    ),
    ("harness_core.invoker.ports.ToolCatalog", "harness_core.knowledge.bulk_export.ExportingCatalog"): (
        "the bulk export's decorator (G-35): it lists the two export tools beside the grant, so the contract's "
        "'schemas name exactly the grant' and 'an empty grant offers nothing' cannot hold by design, and forwards "
        "every other call to the catalog it wraps (which runs the contract); test_ga19_bulk_export.py BE-5 and "
        "test_ga19_export_through_the_gateway.py test it alone and through RunToolEndpoint"
    ),
    ("harness_core.security.labels.SourceLedger", "harness_core.provenance.ledger.ProvenanceFeed"): (
        "the outcome's provenance feed (ADR-RUN-01 D1 x D3, WP-96): a run whose rules read no ledger is fed only the "
        "session's ProvenanceLedger, for its gathered sources; it labels nothing (source_records is unknown) so the "
        "contract's labelling cases cannot hold by design, and no policy reads it; WP96-OUTCOME tests it"
    ),
    ("harness_core.invoker.ports.ToolCatalog", "app.harness.repositories.RepositoryCatalog"): (
        "repository_fetch's decorator (WP-63 part A): it lists repository_fetch (and repository_push) beside the "
        "grant, so the contract's 'schemas name exactly the grant' and 'an empty grant offers nothing' cannot hold by "
        "design, and forwards every other call to the catalog it wraps (which runs the contract); GITF-09 tests it"
    ),
    ("harness_core.invoker.ports.ToolCatalog", "harness_core.gates.plan.PlanGatedTools"): (
        "a run gate's decorator (G-04): it lists the gate's own submit_plan beside the grant, so the contract's "
        "'schemas name exactly the grant' and 'an empty grant offers nothing' cannot hold by design, and forwards "
        "every other call to the catalog it wraps (which runs the contract); GA-03 tests it on the loop and an engine"
    ),
    ("harness_core.security.secrets.CallSecrets", "harness_core.security.secrets.SecretSet"): (
        "an immutable set that answers itself (a route's model key, a fixed test set): it resolves nothing and has "
        "no teardown, so the contract's resolve and close cases cannot apply; SEC4-1..3 table-test it"
    ),
    ("harness_core.interactions.ports.InteractionWakeup", "harness_core.interactions.memory.NoWakeup"): (
        "the null adapter of a deployment with no notifier: nothing can wake it by design (a holder polls the "
        "journal), so the contract's wake cases cannot apply; it is a sleep for the timeout that answers False"
    ),
    ("harness_core.sandbox.egress.ports.TokenSource", "harness_core.sandbox.egress.sealed.SealedTokenSource"): (
        "opens the credential a trusted caller sealed into the route itself (WP-06): the same operator or MCP-server "
        "secret for every sandbox that holds the route, and nothing for a run-bound ref, so the contract's "
        "per-sandbox cases cannot apply; RoutedTokenSource runs the contract with it, SR-1 table-tests it"
    ),
    ("harness_core.sandbox.mcp_runner.ports.MCPRunner", "harness_core.sandbox.mcp_runner.table.RunnerTable"): (
        "the base every runner table extends, with no way to start a runner (``_start`` is abstract); each concrete "
        "table runs MCPRunnerContract (SandboxMCPRunner on the fake broker and on Docker)"
    ),
    ("app.control_plane.runtime.ports.CredentialBroker", "app.harness.assembly.items.run.PinnedCredentialsUnavailable"): (
        "the null broker of a run admitted without a pin, whose items name no binding (CPD-10; a pinned run gets "
        "BindingCredentialBroker): no secret, so the contract's lease, tombstone and refresh cases cannot apply; IR-CRED "
        "tests that it refuses every lease"
    ),
}


# The durable kernel's own seams (WRK-G4) beside the control layer's.
PORT_PACKAGES = (*CONTROL_LAYER, "kernel/durable")


def _port_modules() -> list[Path]:
    found = [path for package in PORT_PACKAGES for path in modules(f"harness_core/{package}")
             if (PYTHON_ROOT / "harness_core" / package).exists()]
    for root in CONTROL_PLANE_PORT_ROOTS:
        if (PYTHON_ROOT / root).exists():
            found.extend(path for path in modules(root) if "ports" in path.relative_to(PYTHON_ROOT).with_suffix("").parts)
    return found


def _discovered() -> set[str]:
    return {f"{dotted(path)}.{node.name}" for path in _port_modules() for node in top_level_classes(path) if is_protocol(node)}


# A hang guard, not a bound: the first case a test worker runs imports every production module of the app.
HEAVY_TIMEOUT_S = 180


@cache
def _production_classes() -> dict[str, type]:
    found: dict[str, type] = {}
    for root in PRODUCTION_ROOTS:
        if not (PYTHON_ROOT / root).exists():
            continue
        for path in modules(root):
            if path.name == "__main__.py":
                continue
            module = importlib.import_module(dotted(path))
            for name, value in vars(module).items():
                # A private class is a helper its public adapter composes; the adapter's contract run covers it.
                public = not name.startswith("_") and not getattr(value, "_is_protocol", False)
                if public and inspect.isclass(value) and value.__module__ == module.__name__:
                    found[f"{module.__name__}.{name}"] = value
    return found


def _required(protocol: type) -> dict[str, Callable[..., object]]:
    return {name: member for name, member in vars(protocol).items() if not name.startswith("_") and callable(member)}


def _parameters(function: Callable[..., object]) -> list[str]:
    variadic = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    return [p.name for p in list(inspect.signature(function).parameters.values())[1:] if p.kind not in variadic]


def _has_attribute(cls: type, name: str) -> bool:
    """A protocol's property: a class attribute, an annotated field, or an attribute ``__init__`` sets."""
    if hasattr(cls, name) or any(name in getattr(k, "__annotations__", {}) for k in cls.__mro__):
        return True
    return f"self.{name} =" in _source(cls)


@cache
def _source(cls: type) -> str:
    # Read once per class: every port with a property asks it of every production class.
    try:
        return inspect.getsource(cls)
    except (OSError, TypeError):
        return ""


def _implements(cls: type, protocol: type) -> bool:
    required = _required(protocol)
    if not required:
        return False
    properties = [name for name, member in vars(protocol).items() if isinstance(member, property)]
    if not all(_has_attribute(cls, name) for name in properties):
        return False
    for name, theirs in required.items():
        ours = getattr(cls, name, None)
        if not callable(ours) or inspect.iscoroutinefunction(ours) != inspect.iscoroutinefunction(theirs):
            return False
        if _parameters(ours) != _parameters(theirs):
            return False
    return True


def _production_adapters(port: str) -> list[str]:
    protocol = _load(port)
    return sorted(name for name, cls in _production_classes().items() if _implements(cls, protocol))


def _load(qualified: str) -> type:
    module, _, name = qualified.rpartition(".")
    return getattr(importlib.import_module(module), name)


@cache
def _test_sources() -> dict[str, str]:
    return {
        str(path.relative_to(PYTHON_ROOT)): path.read_text(encoding="utf-8")
        for path in sorted((PYTHON_ROOT / "tests").rglob("test_*.py"))
    }


@cache
def _test_bases(path: str) -> frozenset[str]:
    """The names the classes of one test file subclass."""
    tree = ast.parse(_test_sources()[path])
    return frozenset(
        base.id for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        for base in node.bases if isinstance(base, ast.Name)
    )


def _contract_names(contract: str) -> frozenset[str]:
    """The contract and the contracts that extend it (``BudgetLedgerContract`` runs every ``BudgetAccountsContract``
    case)."""
    names: set[str] = set()
    pending = [_load(contract)]
    while pending:
        cls = pending.pop()
        if cls.__module__.startswith(CONTRACT_PACKAGES) and cls.__name__ not in names:
            names.add(cls.__name__)
            pending.extend(cls.__subclasses__())
    return frozenset(names)


def _subclassed_in_tests(contract: str) -> list[str]:
    names = _contract_names(contract)
    # Parsed only where the text names the contract: a file cannot subclass what it does not name, and parsing
    # the whole tree took 14 s of whichever case a test worker ran first.
    return [
        path for path, text in _test_sources().items()
        if any(name in text for name in names) and names & _test_bases(path)
    ]


def _runs_against(contract: str, adapter: str) -> bool:
    name = adapter.rpartition(".")[2]
    return any(name in _test_sources()[u] for u in _subclassed_in_tests(contract))


def test_every_port_is_registered_or_exempt() -> None:
    discovered = _discovered()
    unregistered = sorted(discovered - PORTS.keys() - EXEMPT.keys())
    stale = sorted((PORTS.keys() | EXEMPT.keys()) - discovered)
    assert unregistered == [], f"new ports need an in-memory adapter and a contract suite: {unregistered}"
    assert stale == [], f"no longer ports; remove from PORTS/EXEMPT: {stale}"


@pytest.mark.parametrize("port", sorted(PORTS))
def test_the_adapter_implements_every_method_of_its_port(port: str) -> None:
    adapter_path, _ = PORTS[port]
    protocol, adapter = _load(port), _load(adapter_path)
    required = {
        name for name, member in vars(protocol).items() if not name.startswith("_") and callable(member)
    }
    missing = sorted(name for name in required if not callable(getattr(adapter, name, None)))
    assert missing == [], f"{adapter_path} lacks {missing} of {port}"
    for name in required:
        theirs, ours = getattr(protocol, name), getattr(adapter, name)
        assert inspect.iscoroutinefunction(theirs) == inspect.iscoroutinefunction(ours), (
            f"{adapter_path}.{name}: async-ness differs from {port}"
        )


@pytest.mark.parametrize("port", sorted(PORTS))
def test_a_test_runs_the_contract_against_the_adapter(port: str) -> None:
    adapter_path, contract = PORTS[port]
    users = _subclassed_in_tests(contract)
    assert users, f"no test subclasses {contract}"
    assert _runs_against(contract, adapter_path), f"none of {users} runs {contract} against {adapter_path}"


@pytest.mark.timeout(HEAVY_TIMEOUT_S)
@pytest.mark.parametrize("port", sorted(PORTS))
def test_every_production_adapter_runs_its_ports_contract(port: str) -> None:
    _, contract = PORTS[port]
    outside = [
        adapter for adapter in _production_adapters(port)
        if (port, adapter) not in NOT_UNDER_CONTRACT and not _runs_against(contract, adapter)
    ]
    assert outside == [], f"production adapters of {port} that no test runs {contract} against: {outside}"


@pytest.mark.timeout(HEAVY_TIMEOUT_S)
def test_every_exemption_names_a_production_adapter_of_its_port() -> None:
    stale = sorted(
        (port, adapter) for port, adapter in NOT_UNDER_CONTRACT
        if port not in PORTS or adapter not in _production_adapters(port)
    )
    assert stale == [], f"no longer a production adapter of the port; remove from NOT_UNDER_CONTRACT: {stale}"


@pytest.mark.timeout(HEAVY_TIMEOUT_S)
def test_the_scanner_finds_production_adapters_and_nothing_else() -> None:
    journal = _production_adapters("harness_core.journal.ports.JournalPort")
    live_bus = _production_adapters("harness_core.journal.ports.LiveBusPort")
    assert "app.harness.journal_node.client.NodeJournal" in journal
    assert "app.harness.livebus.RedisLiveBus" in live_bus
    scanned = _production_classes()
    assert not [name for name in scanned if ".testing." in name or name.rpartition(".")[2].startswith("_")]
    assert "harness_core.journal.ports.JournalPort" not in scanned
