import { envFlag } from '../../libs/utils/env-flags';
import type {
  InboxCountPublisher,
  InboxNotices,
  InboxStore,
  RunEndNotifier,
} from './ports/inbox';
import type { SkillProposalReader } from './application/inbox-sources';
import type { RunWatchRegistry } from './application/run-end-notices';
import {
  auditLinksIdle,
  createAuditChainStore,
  type HarnessAuditChain,
} from '../../libs/repositories/agent-harness/audit';
import type { OperatorStops, OrgStopReader } from './ports/operator-stops';
import { bindOperatorControls } from './binders/operator-controls';
import { bindRunLists } from './binders/run-lists';
import { conversationRunStatesMiddleware } from './transport/conversation-run-states';
import type { RunListService } from './application/run-lists';
import type { ConversationSeenMarks } from './ports/run-lists';
import type { ToolRuleWriter } from './ports/tool-rules';
import type { WaitingRunNotices } from './ports/approval-links';
import type { PersonNoticeChannels } from './ports/person-notices';
import { MemoryService } from './application/memory-service';
import {
  ChangeOutbox,
  ControlPlaneLog,
  ControlPlaneTransaction,
  MongoControlPlaneTransaction,
  createControlPlaneWriteTransaction,
  createHeadRevisions,
  MongoOrgHeadLeases,
  OrderedStepsTransaction,
  parseControlPlanePin,
} from '../control-plane';
import type { BindingServiceDependencies } from '../control-plane';
import { HeadCachedRolloutSettings } from './adapters/configuration/head-cached-rollout-settings';
import { ChainConfigAuditLog } from './adapters/chain-config-audit-log';
import { AuditChainStore } from './ports/audit';
import {
  MemoryEraser,
  MemoryStore,
  PersonalMemoryChoice,
} from './ports/memory-store';
import type { RunFeedbackService } from './application/run-feedback';
import { HttpSandboxIsolationProbe } from './adapters/http/http-sandbox-isolation-probe';
import { SandboxReaper } from './ports/sandbox-reaper';
import type { Request, RequestHandler } from 'express';
import { Container } from 'inversify';
import type { StoreSession } from '../../libs/repositories/agent-harness/durable-transaction';
import { hostname } from 'node:os';
import { randomUUID } from 'node:crypto';
import { ExecutionService } from './application/execution-service';
import { SessionRunService } from './application/session-run-service';
import { ActivityPanelSetting } from './ports/activity-panel';
import { ActivityFrame } from './projection/activity';
import { ApproverDirectory } from './ports/approver-directory';
import { RunWakeup } from './ports/run-wakeup';
import { noRunReady, RunReadyNotifier } from './ports/run-ready';
import { RunAdmissionService } from './application/run-admission-service';
import { RunSweeper } from './application/run-sweeper';
import { createRunAdmissionStore } from '../../libs/repositories/agent-harness/run-admission-store';
import { createDispatchOutbox } from '../../libs/repositories/agent-harness/dispatch-outbox';
import { createLeaderLease } from '../../libs/repositories/agent-harness/leader-lease';
import { createPendingReaps } from '../../libs/repositories/agent-harness/pending-reaps';
import { createControlPlaneLiveness } from '../../libs/repositories/agent-harness/control-plane-liveness';
import { Logger } from '../../libs/services/logger.service';
import { ChatSessionRepository } from './ports/chat-session-repository';
import { ConversationWriter } from './ports/conversation-writer';
import { HistorySource } from './ports/history-source';
import { AgentAccessPolicy } from './ports/agent-access';
import {
  currentJournalCapability,
  transactionsAvailable,
  JOURNAL_DETAIL,
  describeAgentHarnessReadiness,
  isAgentHarnessInfrastructureReady,
  JournalCapability,
  recordCapabilitySettlement,
  recordJournalCapability,
  recordOrgHarnessTurnedOn,
  erasureMayRun,
  erasureOnDelete,
  orgHarnessOn,
  type OrgHarnessReader,
  runHistoryByReferenceEnabled,
} from './config';
import { storePreparedGauge } from './adapters/telemetry/store-preparation-metrics';
import type { OrgCapabilityRepair } from './application/org-capability-repair';
import {
  storeUnreachable,
  writablePrimary,
  migrateHarnessStore,
  mongoBootMigration,
  type HarnessStoreMigration,
} from '../../libs/repositories/agent-harness/journal-startup/mongo';
import {
  migrateErasureStore,
  migrateScheduleStore,
} from '../../libs/repositories/agent-harness/migrations/mongo';
import { createJournalDurabilityProbe } from '../../libs/repositories/agent-harness/journal-durability-probe';
import { DispatchCapabilityProbe } from './adapters/configuration/dispatch-capability-probe';
import {
  CapabilityLogger,
  HarnessCapabilityService,
} from './application/capability-service';
import { ReleaseKeyRevocationReader } from './application/release-key-revocations';
import { KeyValueStoreService } from '../../libs/services/keyValueStore.service';
import { HarnessRollout } from './application/harness-rollout';
import {
  PlatformCollaboratorTurns,
  PlatformHarnessRolloutSettings,
} from './adapters/configuration/platform-rollout-settings';
import { CollaboratorTurnsSetting } from './ports/collaborator-turns';
import { NextRunService } from './application/next-run-service';
import { AiModelsFamilyResolver } from './adapters/configuration/ai-models-family-resolver';
import type { OrgSettings } from './ports/org-settings';
import { singleStoreOrgSettings } from './adapters/configuration/single-store-org-settings';
import { HarnessRolloutPolicy } from './ports/harness-rollout';
import { PayloadReferences, PayloadStore } from './ports/payload-store';
import { PayloadReferencingJournalStore } from './application/payload-referencing-journal-store';
import { SnapshotRecordingJournalStore } from './application/snapshot-recording-journal-store';
import { createSandboxSnapshotIndex } from '../../libs/repositories/agent-harness/sandbox-snapshots';
import { deploymentRegion, useRegionSource } from './domain/region';
import { useRecordedRunFields } from './domain/recorded-run-fields';
import { useRunFailureMessages } from './projection/transcript';
import type { StoredObjectPurger } from './ports/sandbox-snapshots';
import { JournalService } from './application/journal-service';
import { createJournalStore } from '../../libs/repositories/agent-harness/journal';
import { createPayloadStore } from '../../libs/repositories/agent-harness/payload-store';
import {
  GatewayTokenStores,
  HarnessAuthorityStores,
} from './adapters/configuration/encrypted-config-document-store';
import { GatewayTokenService } from './application/gateway-token-service';
import { HarnessAuthority } from './application/harness-authority';
import { HarnessLogger } from './application/harness-log';
import {
  ConversationReader,
  RunConversationView,
} from './ports/conversation-reader';
import type { SlackThreadPoster } from './application/thread-replies';
import { RecordReadAccess } from './ports/record-access';
import { SharedCitationService } from './application/shared-citations';
import { SharedArtifactService } from './application/shared-artifacts';
import { ArtifactReadAccess } from './ports/artifact-access';
import { SessionRoleLookup } from './ports/session-roles';
import type { OrgMemberLookup, OrgMemberProfiles } from './ports/org-members';
import { LiveBus } from './ports/live-bus';
import { RunStreamService } from './projection/stream-service';
import { createJournalTail } from '../../libs/repositories/agent-harness/journal-tail';
import type { JournalTail } from './ports/journal-tail';
import { CapabilityProbe } from './ports/capability-probe';
import { activeRunIdMiddleware } from './transport/active-run';
import { ChatTurnService, WaitingOnReader } from './application/chat-turns';
import type { RunTimelineService } from './application/run-timeline-service';
import {
  ChatTurnRouteOptions,
  chatTurnMiddleware,
} from './transport/chat-turn-route';
import {
  chatCancelMiddleware,
  ChatCancelRouteOptions,
} from './transport/chat-cancel-route';
import { KeyEncryptionKey } from './ports/data-keys';
import { platformKeyEncryptionKey } from './adapters/crypto/hkdf-key-encryption-key';
import { platformRunKeyDigest } from './adapters/crypto/platform-run-key-digest';
import { RunKeyDigest } from './domain/run-key-digest';
import { createDataKeyStore } from '../../libs/repositories/agent-harness/data-protection';
import { OrgDataKeys } from './application/org-data-keys';
import { EnvelopeCipher } from './application/envelope-cipher';
import { SealedJournalStore } from './application/sealed-journal-store';
import { SessionImportContent } from './application/session-import-content';
import { RunHistoryContent } from './application/run-history-content';
import { RunInputSeal } from './application/run-input-seal';
import {
  RunInputMigration,
  sealStoredRunInputs,
  typedReviewMessages,
} from '../../libs/repositories/agent-harness/migrations/mongo';
import { RunJournalReader } from './application/run-journal-reader';
import { AuditedJournalStore } from './application/audited-journal-store';
import { RecordingJournalStore } from './application/recording-journal-store';
import {
  AuditAnchorService,
  AuditVerifier,
} from './application/audit-anchoring';
import { createAuditAnchorSink } from '../../libs/repositories/agent-harness/audit-anchor-sink';
import { AuditLinkWatch } from './application/audit-link-watch';
import {
  prometheusAdmissionQuotaMetrics,
  prometheusAuditLinkMetrics,
} from './adapters/telemetry/org-contention-metrics';
import { AuditAnchorSink } from './ports/audit';
import type { AgentNameReader, AgentSummaryReader } from './ports/agent-names';
import {
  AGENT_HARNESS_SESSION_RUN_STOPPER,
  type SessionRunStopperPort,
} from './application/session-run-stopper';
import type { ChatTitleReader } from './ports/chat-titles';
import { bindRunFeedback } from './binders/feedback';
import { JournalStore } from './ports/journal-store';
import { SessionParticipantRecorder } from './ports/session-participants';
import {
  ArtifactEraser,
  AttachmentCarriers,
  AttachmentEraser,
  ConversationEraser,
  ConversationStateLookup,
  LiveBusEraser,
  SessionErasureQueue,
  TraceEraser,
  UnsentAttachments,
} from './ports/session-erasure';
import { SessionErasureWorker } from './application/session-erasure';
import { SessionForkService } from './application/session-fork-service';
import { ArtifactCopier, SessionForkStore } from './ports/session-forks';
import { WorkspaceObjectBytes } from './ports/workspace-objects';
import { DURABLE_TRANSACTION_OPTIONS } from '../../libs/repositories/agent-harness/durable-transaction/mongo';
import { ConfigDocumentStore } from './ports/config-document-store';
import {
  OrgDeletedAnnouncer,
  OrgQueryServiceEraser,
  OrgRedisEraser,
} from './ports/principal-erasure';
import type { OrgPrincipalEraser } from './application/principal-erasure';
import { ConversationRetention } from './ports/session-retention';
import { RetentionSweeper } from './application/retention-sweeper';
import { AgentDeletionLookup, AgentErasureQueue } from './ports/agent-erasures';
import { createChildRunStore } from '../../libs/repositories/agent-harness/child-runs';
import { createBudgetLedger } from '../../libs/repositories/agent-harness/budget-ledger';
import { Offering } from './domain/model-access';
import { ConfiguredModels } from './ports/model-access';
import { ModelAccountStore } from './ports/model-accounts';
import { ScheduleFirer } from './application/schedule-firer';
import {
  KnowledgePublicationsSetting,
  PublicationAuthority,
} from './ports/knowledge-publications';
import {
  ActorAuthority,
  AgentSlackBots,
  ScheduledRunsSetting,
  ScheduleNotifier,
  ScheduleStore,
} from './ports/schedules';
import type { IMessageConsumer } from '../../libs/types/messaging.types';
import type { AgentDirectory, MembershipPort } from '../control-plane';
import { HarnessBinder, HarnessBindingContext } from './binders/context';
import {
  AGENT_HARNESS_EDITION,
  noopHarnessEdition,
  startEditionWorkers,
  stopEditionWorkers,
  type HarnessEdition,
} from './edition';
import type { HarnessMigration } from '../../libs/utils/mongo-index-migration';
import {
  bindNextRuns,
  bindRoutingCapabilities,
  bindRunAdmission,
} from './binders/admission';
import { bindControlPlaneCapabilities } from './binders/control-plane-capabilities';
import {
  bindAuthority,
  bindBudgetLedger,
  bindGatewayTokens,
} from './binders/authority';
import { bindRetention, bindSessionErasure } from './binders/erasure';
import { bindPrincipalDeactivation } from './binders/deactivation';
import {
  bindChildRuns,
  bindExecution,
  bindExecutionStore,
} from './binders/execution';
import {
  bindCodingEngines,
  bindEngineSignIns,
  bindOrgMembers,
  bindModelAccess,
} from './binders/models';
import type { CodingEnginePreferences } from './ports/coding-engines';
import type { RepositoryToolsets } from './ports/repository-toolsets';
import type {
  EngineSignInRelay,
  EngineSignInStore,
} from './ports/engine-signins';
import { bindCredentialBindings } from './binders/credential-bindings';
import {
  bindApprovalSettings,
  bindEngineProfiles,
} from './binders/policy-settings';
import { bindDirectoryCaches } from './binders/directory-caches';
import { chatModelGroupsOf } from './binders/group-access';
import type {
  ActorGroupMembership,
  GroupNameReader,
} from './ports/group-access';
import { bindControlPlanePin } from './binders/control-plane-pin';
import { bindControlPlaneAdmin } from './binders/control-plane-admin';
import {
  bindChangeNotifier,
  startChangeNotifier,
  stopChangeNotifier,
  type ChangeEventBroker,
} from './binders/change-notifier';
import { processChangeOutbox } from './adapters/control-plane/process-changes';
import type {
  ConfiguredLlmSource,
  LabsStagesSource,
} from './ports/pin-sources';
import type { PolicyLayers } from '../control-plane';
import { bindPublications } from './binders/publications';
import { bindSandboxReaper } from './binders/sandbox';
import { bindSchedules } from './binders/schedules';
import { bindInbox } from './binders/inbox';
import {
  bindApprovers,
  bindInteractions,
  bindRunContent,
  bindRunViews,
  bindSessionRuns,
} from './binders/session-views';
import { bindSharing } from './binders/sharing';
import {
  bindSessionForks,
  bindSessionSandbox,
  bindSessionRepositories,
  bindSessionWorkspaces,
  bindWorkspaces,
} from './binders/workspaces';
import {
  detachedInterval,
  getCurrentOrgId,
} from '../../libs/context/orgContext';
import type { ComponentCredentialPublisher } from './application/component-credential-publisher';
import {
  AGENT_HARNESS_AUDIT_ANCHORING,
  AGENT_HARNESS_AUDIT_LINK_WATCH,
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_COMPONENT_PUBLISHER,
  AGENT_HARNESS_CAPABILITIES,
  AGENT_HARNESS_DATA_KEYS,
  AGENT_HARNESS_ERASURE_WORKER,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_GATEWAY_TOKENS,
  AGENT_HARNESS_APPROVER_LINK_NOTICES,
  AGENT_HARNESS_MEMORIES,
  AGENT_HARNESS_NEXT_RUNS,
  AGENT_HARNESS_RETENTION_SWEEPER,
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_RUN_STREAMS,
  AGENT_HARNESS_RUN_TIMELINES,
  AGENT_HARNESS_RUN_WATCHERS,
  AGENT_HARNESS_RUN_KEYS,
  AGENT_HARNESS_RUN_SWEEPER,
  AGENT_HARNESS_SANDBOX_REAPER,
  AGENT_HARNESS_SCHEDULE_FIRER,
  AGENT_HARNESS_RUN_FEEDBACK,
  AGENT_HARNESS_SESSION_ERASURES,
  AGENT_HARNESS_SESSION_FORKS,
  AGENT_HARNESS_SESSION_PARTICIPANTS,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
  AGENT_HARNESS_RUN_LISTS,
  AGENT_HARNESS_SHARED_ARTIFACTS,
  AGENT_HARNESS_SHARED_CITATIONS,
  AGENT_HARNESS_AGENT_ERASURES,
  AGENT_HARNESS_OPERATOR_CONTROLS,
  CONTROL_PLANE_CAPABILITY_REPAIR,
  CONTROL_PLANE_TRANSACTION,
} from './binders/tokens';

export * from './binders/tokens';
export { createHarnessAuthority } from './binders/authority';
export { agentDataEraser, orgPrincipalErasers } from './binders/erasure';
export type { HarnessBinder, HarnessBindingContext } from './binders/context';

/** Adapters the harness needs from the modules that own conversations. */
export interface AgentHarnessDependencies {
  /** What an edition adds (binders, routes, migrations); absent: nothing (`noopHarnessEdition`). */
  edition?: HarnessEdition;
  sessions: ChatSessionRepository<StoreSession>;
  conversations: ConversationWriter<StoreSession>;
  history: HistorySource<StoreSession>;
  agentAccess: AgentAccessPolicy;
  rollout: HarnessRolloutPolicy;
  /**
   * Store for sealed bytes: claim-checked journal payloads over 256 KB and
   * worker content over the inline threshold; Mongo by default.
   */
  payloads?: PayloadStore;
  /** Worker credential and attempt-token keyring (encrypted configuration). */
  authority: HarnessAuthorityStores;
  /** The gateway-token keyring (E2). Absent: no gateway-token routes. */
  gatewayTokens?: GatewayTokenStores;
  /**
   * Base URL of the query service, which claims the runs it can start at
   * once (the inline profile). Absent: every run is dispatched to workers.
   */
  inlineClaimer?: () => string;
  /** Reads a finished run's conversation for the stream's final frame. */
  conversationReader?: ConversationReader;
  /** What of it the watcher may read. Absent: a shared stream carries no answer. */
  conversationView?: RunConversationView;
  /** Posts a Slack turn's end into its thread when the bot's stream ended first (D-SLACK-ASK). Absent: never posted. */
  slackThreads?: SlackThreadPoster;
  /**
   * Which records a shared viewer may read now (C-6), for the citations of
   * shared streams and conversation reads. Absent: shared views hide every
   * record citation.
   */
  recordAccess?: RecordReadAccess;
  /** Saves a durable card's "Don't ask again" as the person's own tool rule (BUGS-B). Absent: it is refused. */
  toolRules?: ToolRuleWriter;
  /** The app's mail and Slack paths to one person, for the notices a bound feature sends. Absent: it sends none. */
  noticeChannels?: PersonNoticeChannels;
  /**
   * Which artifacts a reader who does not own a conversation may see (SS0).
   * Absent: shared views hide every artifact.
   */
  artifactAccess?: ArtifactReadAccess;
  /** Participants' roles, so a refused turn says why (SS0); absent: it stays not found. */
  roles?: SessionRoleLookup;
  /** Whether an org lets collaborators send turns (SS2); absent: no org does. */
  collaboratorTurns?: CollaboratorTurnsSetting;
  /** The LiveBus the workers publish token deltas to; absent: journal-only streams. */
  liveBus?: LiveBus | null;
  /**
   * The activity panel (UX5): its org Labs switch and the activity bus the
   * workers publish command output to. Absent: no panel routes; no bus:
   * journal-only snapshots.
   */
  activity?: {
    setting: ActivityPanelSetting;
    bus: LiveBus<ActivityFrame> | null;
  };
  /** Wakes a worker holding a call for approval (P2.6c); absent: it sees the decision at its next poll. */
  wakeup?: RunWakeup | null;
  /** Tells idle claimers a run became claimable, after its commit (LAT1); absent: they find it at their next poll. */
  runReady?: RunReadyNotifier | null;
  /**
   * Who besides a run's actor decides its approvals (P2.6c): the org's admins deny; bound as
   * `AGENT_HARNESS_APPROVERS` unless an edition bound its own. Absent: only the run's actor.
   */
  approvers?: ApproverDirectory;
  /** The approvals document (P2.11e); absent: the admin approvals routes answer 503. */
  approvalSettings?: {
    document: ConfigDocumentStore<unknown>;
  };
  /**
   * G-41 (GA-21): the org's user groups, read live: who may use the harness,
   * each engine and each model when the org's access section names groups,
   * and the ids a save may name. Absent (or without the policy store): an
   * access save answers 503 and nothing is limited by group.
   */
  groupMembership?: ActorGroupMembership & {
    unknownGroups(orgId: string, ids: readonly string[]): Promise<string[]>;
    /** ADR-UI-03: the names of the groups an admin answer holds; absent: answered without them. */
    groupNames?: GroupNameReader['groupNames'];
  };
  /**
   * The control plane's policy store (CPD-4, `MongoPolicyStore`). Absent: the
   * five admin policy settings stay on their KV documents, as with
   * `CONTROL_PLANE_POLICY_STORE=off`.
   */
  policyStore?: PolicyLayers;
  /** The model accounts in the credential vault (P2.4b) a run may be pinned to; absent: none. */
  modelAccounts?: ModelAccountStore;
  /** Where the default chat's coding-engine choices live (ENGPICK); absent: its Agent mode offers no engine. */
  codingEnginePreferences?: CodingEnginePreferences;
  /**
   * GIT-FLOW (WP-26): the GitHub toolset instances a person may use, asked of the toolset store with their own
   * credential. Absent: a chat cannot be given a repository (its routes answer 503).
   */
  repositoryToolsets?: RepositoryToolsets;
  /**
   * People's own Claude accounts in Claude Code (SIGNIN): the user and org
   * records, and the relay to the worker holding a sign-in. Absent: nobody
   * can use one (the routes answer 503); without the relay, no sign-in can finish.
   */
  engineSignIns?: { store: EngineSignInStore; relay?: EngineSignInRelay };
  /** Who belongs to an org (QA-26): an admin route naming another user resolves them in the caller's org first. */
  orgMembers?: OrgMemberLookup;
  /**
   * ADR-UI-01 D2: where each person's "seen up to" mark on a conversation is kept; absent: on the conversation itself,
   * through the conversation repository.
   */
  seenMarks?: ConversationSeenMarks;
  /**
   * The names and chat titles the run lists and the inbox show, read from
   * the org's own records when they are read. Absent: they are shown
   * unnamed. `agents` names agents the same way.
   */
  auditNames?: {
    members: OrgMemberProfiles;
    chats: ChatTitleReader;
    agents?: AgentNameReader;
  };
  /** The credential vault's KV store and the platform cipher for bindings (CPD-8); absent: the binding routes answer 503. */
  credentialBindings?: Omit<BindingServiceDependencies, 'transaction'>;
  /**
   * Model access (A1): the configured models an engine run pins concretely,
   * checked against the signed vendor rules. Absent: engine runs and runs
   * naming a model account are refused (503), never pinned unchecked.
   */
  modelAccess?: {
    models: ConfiguredModels;
    /** The install's offering; absent: `AGENT_HARNESS_OFFERING` (an invalid value stops the process). */
    offering?: Offering;
  };
  /**
   * What a pin reads besides policy (CPD-6): the deployment's configured LLMs
   * and the org's Labs stages. Absent: a pin holds no `llm`, no models
   * ceiling and only the two rollout switches.
   */
  pin?: { models: ConfiguredLlmSource; stages: LabsStagesSource };
  /** Probes of what this deployment runs beyond the journal and dispatch (e.g. the LiveBus). */
  capabilityProbes?: readonly CapabilityProbe[];
  /** The broker `control-plane-events` is consumed from (CPD-6); absent: this process hears only its own writes. */
  changeEvents?: ChangeEventBroker | null;
  /** The control-plane write transaction (CPD-1); absent: the deployment's (`createControlPlaneTransaction`). */
  controlPlane?: ControlPlaneTransaction;
  /**
   * Wraps the per-org data keys. Absent: HKDF from the platform secret
   * (self-hosted); a managed profile passes its KMS-backed key here.
   */
  keyEncryptionKey?: KeyEncryptionKey;
  /** What a run stores for its idempotency key. Absent: keyed by the platform secret. */
  runKeys?: RunKeyDigest;
  /**
   * The stores other modules own that session erasure (E7) deletes from:
   * the conversation's documents, the artifact registry, the LiveBus.
   * Absent: no erasure worker runs (tests that bind only the harness).
   */
  /**
   * Where the sandbox broker answers (`/services/endpoints` `sandboxBroker`), so the
   * run sweeper reaps the sandboxes of runs it expires (E1). Absent: no reaping here.
   */
  sandboxBrokerEndpoint?: () => Promise<string | null>;
  /**
   * "Continue privately" (SS3): the conversation side of a fork, the artifact
   * registry's copies and the workers' object store (to re-seal a session's
   * files under the fork's key). Absent: the fork routes answer 503.
   */
  forks?: {
    store: SessionForkStore<StoreSession>;
    artifacts: ArtifactCopier;
    objects: WorkspaceObjectBytes;
  };
  /**
   * Scheduled and triggered runs (P8a): who a schedule acts as (read at each
   * firing), its Labs switch, how its actor is told about an approval, and
   * the broker consumer connector triggers read. Absent: the schedule routes
   * answer 503 and nothing fires.
   */
  schedules?: {
    actors: ActorAuthority;
    setting: ScheduledRunsSetting;
    notifier?: ScheduleNotifier;
    /** Which agents have a Slack bot, for the schedule form's Slack choice (TR-07). */
    slackBots?: AgentSlackBots;
    /** The list's agent names and live versions (ADR-UI-03); absent: listed without them. */
    agents?: AgentSummaryReader;
    store?: ScheduleStore;
    connectorEvents?: {
      consumer: () => IMessageConsumer;
      streams: () => string[];
    };
  };
  /**
   * G2 (ADR-INBOX-01): the Inbox's ties to the rest of the app. `notifier` tells a person their
   * run ended unseen (the schedule notifier's channels); `publisher` pushes their counts over the notification socket;
   * `skills` lists the skill proposals waiting on an admin. Absent parts are left out of the feed.
   */
  inbox?: {
    store?: InboxStore;
    notifier?: RunEndNotifier;
    publisher?: InboxCountPublisher;
    skills?: SkillProposalReader;
    notices?: InboxNotices;
  };
  /**
   * CPD-9: the `entity-events` consumer (its own group per process) whose
   * `teamMembershipChanged` / `agentChanged` drop the directory caches, and the
   * directory those caches front. Absent: no directory caches are bound.
   */
  directory?: {
    events: () => IMessageConsumer;
    membership: MembershipPort;
    agents: AgentDirectory;
  };
  /**
   * Knowledge publications (P2.13): the org's Labs switch and the query
   * service that owns them. Absent: the publication routes answer 503.
   */
  publications?: {
    setting: KnowledgePublicationsSetting;
    authority: PublicationAuthority;
  };
  /**
   * Governed memory (P7.3): for tests, the store (Mongo by default). Absent:
   * memory off and the memory routes answer 503; erasure still deletes any
   * memory the Mongo store holds.
   */
  memory?: {
    store?: MemoryStore & MemoryEraser;
    /** Each person's own memory switch; absent: nobody can turn their memory off. */
    choices?: PersonalMemoryChoice;
  };
  erasure?: {
    /** Also read before every erasure step: a live conversation is never erased (E7B). */
    conversations: ConversationEraser & ConversationStateLookup;
    artifacts: ArtifactEraser;
    /** The session's traces in Opik, through the query service (CLEANUP-OPIK); absent: none are erased. */
    traces?: TraceEraser;
    liveBus: LiveBusEraser | null;
    /** The conversation module's side of org retention (T5). */
    retention: ConversationRetention<StoreSession>;
    /** Agent erasure intents and failed erasures (H4, H5); default Mongo `agentErasures`. */
    agentErasures?: AgentErasureQueue;
    /** Whether an agent's delete committed: the query service's tombstone (H5). */
    agentDeletions: AgentDeletionLookup;
    /** The org-wide erase routes and hooks an org's erasure job is built from (`HarnessEdition.orgErasure`). */
    orgQueryService?: OrgQueryServiceEraser;
    orgPrincipals?: readonly OrgPrincipalEraser[];
    orgRedis?: OrgRedisEraser;
    orgDeletedAnnouncer?: OrgDeletedAnnouncer;
    /** Deletes a stored object (OWD-12: sandbox snapshots past their TTL); absent: none deleted. */
    snapshotObjects?: StoredObjectPurger;
    /** Uploads waiting for a message, and the messages that carry them (CLEANUP-G5); absent: none collected. */
    unsentAttachments?: {
      uploads: UnsentAttachments;
      carriers: AttachmentCarriers;
      eraser: AttachmentEraser;
    };
  };
}

/**
 * The journal every harness service shares: audited, sealed at rest, one
 * data-key cache, and the org audit chain with its anchors.
 */
export interface HarnessJournal {
  store: JournalStore<StoreSession>;
  service: JournalService;
  keys: OrgDataKeys;
  cipher: EnvelopeCipher;
  payloads: PayloadStore;
  /** Run histories, sealed beside the journal's payloads (QA22-F2). */
  history: RunHistoryContent;
  /** The query of a run's document and receipts, sealed with the same keys (LCFIX). */
  runInput: RunInputSeal;
  /** One reader of run journals for the Node's commits and streams (QA22-F3, QA22-F4). */
  runJournal: RunJournalReader;
  audit: HarnessAuditChain;
  anchors: AuditAnchorSink;
  verifier: AuditVerifier;
}

const hasPayloadReferences = (
  payloads: PayloadStore,
): payloads is PayloadStore & PayloadReferences<StoreSession> =>
  'markReferenced' in payloads;

export function createHarnessJournal(
  dependencies: Pick<
    AgentHarnessDependencies,
    'payloads' | 'keyEncryptionKey' | 'edition'
  >,
  logger?: HarnessLogger,
): HarnessJournal {
  const keys = new OrgDataKeys({
    store: createDataKeyStore(),
    kek: dependencies.keyEncryptionKey ?? platformKeyEncryptionKey(),
    logger,
  });
  const cipher = new EnvelopeCipher(keys);
  const payloads = dependencies.payloads ?? createPayloadStore();
  const audit = createAuditChainStore(logger);
  const sealed = new SealedJournalStore(createJournalStore(), cipher, logger);
  const anchors =
    dependencies.edition?.auditAnchors?.() ?? createAuditAnchorSink();
  const recorders = (dependencies.edition?.journalRecorders ?? []).map(
    (recorder) => recorder(),
  );
  const snapshots = new SnapshotRecordingJournalStore(
    hasPayloadReferences(payloads)
      ? new PayloadReferencingJournalStore(sealed, payloads)
      : sealed,
    createSandboxSnapshotIndex(),
    deploymentRegion(),
  );
  const store = new AuditedJournalStore(
    recorders.length > 0
      ? new RecordingJournalStore(snapshots, recorders)
      : snapshots,
    audit,
  );
  const service = new JournalService(payloads, cipher, undefined, recorders);
  return {
    keys,
    cipher,
    payloads,
    history: new RunHistoryContent(
      cipher,
      payloads,
      hasPayloadReferences(payloads) ? payloads : null,
    ),
    runInput: new RunInputSeal(cipher),
    runJournal: new RunJournalReader({
      journal: store,
      journals: service,
      logger,
    }),
    audit,
    anchors,
    verifier: new AuditVerifier(audit, anchors, logger),
    store,
    service,
  };
}

/**
 * The deployment's control-plane write transaction (CPD-1): one Mongo
 * transaction on a replica set, ordered steps with a repair on standalone
 * Mongo (02-lld §6.4). Either way the audit entry goes on the org's chain and
 * the change event in the shared outbox.
 */
export function createControlPlaneTransaction(
  log?: ControlPlaneLog,
  chain: AuditChainStore<StoreSession> = createAuditChainStore(),
  env: Readonly<Record<string, string | undefined>> = process.env,
  outbox: ChangeOutbox = processChangeOutbox,
): ControlPlaneTransaction {
  const audit = new ChainConfigAuditLog(chain);
  return createControlPlaneWriteTransaction({
    audit,
    outbox,
    ...(log !== undefined ? { log } : {}),
    mongo: () =>
      describeAgentHarnessReadiness(env).transactions
        ? new MongoControlPlaneTransaction({
            audit,
            outbox,
            transaction: DURABLE_TRANSACTION_OPTIONS,
            ...(log !== undefined ? { log } : {}),
          })
        : new OrderedStepsTransaction({
            audit,
            outbox,
            heads: new MongoOrgHeadLeases(),
            ...(log !== undefined ? { log } : {}),
          }),
  });
}

/** Every capability this process can probe; the rest keep their least value. */
export function createHarnessCapabilities(
  logger?: CapabilityLogger,
  env: Readonly<Record<string, string | undefined>> = process.env,
  probes: readonly CapabilityProbe[] = [],
): HarnessCapabilityService {
  return new HarnessCapabilityService(
    [
      createJournalDurabilityProbe(),
      new DispatchCapabilityProbe(env),
      ...probes,
    ],
    logger,
  );
}

/**
 * Where the rollout reads each org's Labs settings and AI models: by default the one platform document and the one
 * model list, as a deployment that holds one org keeps them.
 */
export interface HarnessRolloutSources {
  orgSettings?: OrgSettings;
  /** Org and instance stops beyond the env switch (ADR-OPS-01); absent: none. */
  orgStops?: OrgStopReader;
}

/** The rollout's org stops an edition reads from the settings store (`HarnessEdition.rolloutStops`), if any. */
export const harnessOrgStops = (
  edition: Pick<HarnessEdition, 'rolloutStops'>,
  settings: KeyValueStoreService,
): Pick<HarnessRolloutSources, 'orgStops'> =>
  edition.rolloutStops === null
    ? {}
    : { orgStops: edition.rolloutStops(settings) };

/** The one rollout policy for admissions and for what the UI is told. */
export function createHarnessRollout(
  settings: KeyValueStoreService,
  env: Readonly<Record<string, string | undefined>> = {
    CONTROL_PLANE_PIN: process.env.CONTROL_PLANE_PIN,
  },
  sources: HarnessRolloutSources = {},
): HarnessRolloutPolicy {
  const orgSettings = sources.orgSettings ?? singleStoreOrgSettings(settings);
  const labs = new PlatformHarnessRolloutSettings(
    settings,
    process.env,
    sources.orgStops ?? null,
    orgSettings,
  );
  return new HarnessRollout(
    // CONTROL_PLANE_PIN shadow or on: the Labs document is read once per org head (CPD-5).
    parseControlPlanePin(env, envFlag('AGENT_HARNESS_WORKER_ENABLED')) === 'off'
      ? labs
      : new HeadCachedRolloutSettings(labs, createHeadRevisions()),
    new AiModelsFamilyResolver(orgSettings),
  );
}

/** The org predicate's reader (ADR-OPS-08) over the rollout policy every admission uses. */
export const orgHarnessReaderOf =
  (rollout: Pick<HarnessRolloutPolicy, 'decideForOrg'>): OrgHarnessReader =>
  async (orgId) =>
    (await rollout.decideForOrg(orgId)).harnessOn;

export function createCollaboratorTurns(
  settings: KeyValueStoreService,
): CollaboratorTurnsSetting {
  return new PlatformCollaboratorTurns(settings);
}

const bindCapabilities: HarnessBinder = ({ container, dependencies }) => {
  if (container.isBound(AGENT_HARNESS_CAPABILITIES)) return;
  container
    .bind<HarnessCapabilityService>(AGENT_HARNESS_CAPABILITIES)
    .toConstantValue(
      createHarnessCapabilities(
        container.isBound('Logger')
          ? container.get<Logger>('Logger')
          : undefined,
        process.env,
        [
          ...(dependencies.capabilityProbes ?? []),
          ...sandboxIsolationProbes(container, dependencies, process.env),
        ],
      ),
    );
};

/**
 * Every feature's binder, in the order a binding made at bind time reads the
 * ones before it (the execution service reads capabilities, next runs and
 * shared citations; run streams read shared citations and artifacts).
 */
export const AGENT_HARNESS_BINDERS: readonly HarnessBinder[] = [
  bindControlPlanePin,
  bindChangeNotifier,
  bindExecutionStore,
  bindSandboxReaper,
  bindChildRuns,
  bindSessionErasure,
  bindModelAccess,
  bindEngineSignIns,
  bindOrgMembers,
  bindCredentialBindings,
  bindEngineProfiles,
  bindCodingEngines,
  bindRetention,
  bindSharing,
  bindCapabilities,
  bindNextRuns,
  bindExecution,
  bindAuthority,
  bindGatewayTokens,
  bindBudgetLedger,
  bindRunContent,
  bindWorkspaces,
  bindSessionForks,
  bindSessionRuns,
  bindSessionWorkspaces,
  bindSessionSandbox,
  bindSessionRepositories,
  bindApprovers,
  bindInteractions,
  bindApprovalSettings,
  bindRunViews,
  bindControlPlaneCapabilities,
  bindRunAdmission,
  bindRoutingCapabilities,
  bindSchedules,
  bindPrincipalDeactivation,
  bindOperatorControls,
  bindRunLists,
  bindPublications,
  bindDirectoryCaches,
  bindControlPlaneAdmin,
  bindRunFeedback,
  bindInbox,
];

function createBindingContext(
  container: Container,
  dependencies: AgentHarnessDependencies,
  edition: HarnessEdition,
): HarnessBindingContext {
  const dispatches = createDispatchOutbox(dependencies.runReady ?? noRunReady);
  const logger = container.isBound('Logger')
    ? container.get<Logger>('Logger')
    : undefined;
  if (!container.isBound(AGENT_HARNESS_DATA_KEYS)) {
    container
      .bind<HarnessJournal>(AGENT_HARNESS_DATA_KEYS)
      .toConstantValue(createHarnessJournal(dependencies, logger));
  }
  const journal = container.get<HarnessJournal>(AGENT_HARNESS_DATA_KEYS);
  if (!container.isBound(CONTROL_PLANE_TRANSACTION)) {
    container
      .bind<ControlPlaneTransaction>(CONTROL_PLANE_TRANSACTION)
      .toConstantValue(
        dependencies.controlPlane ??
          createControlPlaneTransaction(logger, journal.audit),
      );
  }
  const controlPlane = container.get<ControlPlaneTransaction>(
    CONTROL_PLANE_TRANSACTION,
  );
  if (!container.isBound(AGENT_HARNESS_RUN_KEYS)) {
    container
      .bind<RunKeyDigest>(AGENT_HARNESS_RUN_KEYS)
      .toConstantValue(dependencies.runKeys ?? platformRunKeyDigest());
  }
  const runKeys = container.get<RunKeyDigest>(AGENT_HARNESS_RUN_KEYS);
  const admissionStore = createRunAdmissionStore({
    sessions: dependencies.sessions,
    history: dependencies.history,
    conversations: dependencies.conversations,
    dispatches,
    journal: journal.store,
    journals: journal.service,
    importContent: new SessionImportContent(journal.cipher, journal.payloads),
    // Written by reference unless the operator is rolling back; read either way (the execution service's reader).
    ...(runHistoryByReferenceEnabled()
      ? { historyContent: journal.history }
      : {}),
    inputs: journal.runInput,
    runKeys,
    ...(logger !== undefined ? { logger } : {}),
    quotaMetrics: prometheusAdmissionQuotaMetrics,
  });
  const budgets = createBudgetLedger(edition.costEntries?.() ?? null);
  const childRuns = createChildRunStore({
    dispatches,
    journal: journal.store,
    budgets,
    runKeys,
    inputs: journal.runInput,
  });
  let sharedTail: JournalTail | null = null;
  return {
    container,
    dependencies,
    logger,
    journal,
    controlPlane,
    dispatches,
    admissionStore,
    budgets,
    childRuns,
    runKeys,
    journalTail: () => (sharedTail ??= createJournalTail(logger)),
  };
}

/**
 * Binds every harness service, keeping any binding made before (a test
 * double or another module). The edition's binders run first, so what they
 * bind is what the built-in binders keep. `binders` is for tests that add one.
 */
export function bindAgentHarness(
  container: Container,
  dependencies: AgentHarnessDependencies,
  binders: readonly HarnessBinder[] = AGENT_HARNESS_BINDERS,
): void {
  const edition = dependencies.edition ?? noopHarnessEdition;
  if (edition.region !== null) useRegionSource(edition.region);
  useRecordedRunFields(edition.recordedRunFields);
  useRunFailureMessages(edition.runFailureMessages);
  if (!container.isBound(AGENT_HARNESS_EDITION)) {
    container
      .bind<HarnessEdition>(AGENT_HARNESS_EDITION)
      .toConstantValue(edition);
  }
  const context = createBindingContext(container, dependencies, edition);
  for (const bind of [...edition.binders, ...binders]) bind(context);
}

/** The wait before each further try of a store preparation that failed. */
export interface StorePreparationRetry {
  wait(attempt: number): Promise<void>;
}

/** Doubling from one second to eight: a returning store is used within eight seconds. */
export const STORE_PREPARATION_RETRY: StorePreparationRetry = {
  wait: (attempt) =>
    new Promise((resolve) => {
      setTimeout(resolve, Math.min(1_000 * 2 ** (attempt - 1), 8_000));
    }),
};

export interface StorePreparationHooks {
  /** Called once a preparation that first failed for the harness alone succeeds in the background. */
  whenPrepared?: () => void;
  /** Whether the harness's store is prepared (a gauge). */
  prepared?: (prepared: boolean) => void;
  /** The index migrations each preparation runs; absent: the built-in ones (`harnessMigrations`). */
  migrations?: readonly HarnessMigration[];
}

let backgroundPreparation: AbortController | null = null;

/**
 * Idempotent store preparation, then the first capability probe (journal
 * durability among them). Skipped unless the deployment asked for the
 * harness, so a flag-off install is not touched.
 *
 * Two failures, two answers. MongoDB unreachable or without a primary: the
 * whole application needs it, so this tries again and returns only when it
 * answers; the caller listens after this. MongoDB answering while the
 * harness's own preparation cannot complete (no replica set, no majority,
 * missing privileges, an index build refused): the rest of PipesHub must not
 * go down with it, so this returns at once with the harness recorded
 * unavailable (`preparation_failed`, or the journal's own reason), which
 * admission and the health route report, and keeps trying in the background;
 * the harness turns itself on when one succeeds (CPD-6 F-B).
 */
export async function prepareAgentHarnessStore(
  logger: Pick<Logger, 'info' | 'warn' | 'error'>,
  env: Readonly<Record<string, string | undefined>> = process.env,
  capabilities: HarnessCapabilityService = createHarnessCapabilities(
    logger,
    env,
  ),
  retry: StorePreparationRetry = STORE_PREPARATION_RETRY,
  hooks: StorePreparationHooks = {},
): Promise<void> {
  if (!isAgentHarnessInfrastructureReady(env, null)) return;
  const prepared = hooks.prepared ?? storePreparedGauge;
  for (let attempt = 1; ; attempt += 1) {
    const failure = await tryPrepareStore(
      logger,
      capabilities,
      hooks.migrations,
    );
    if (failure === null) {
      prepared(true);
      logger.info('Agent harness readiness', {
        readiness: describeAgentHarnessReadiness(env),
      });
      return;
    }
    if (!failure.unreachable) {
      // A journal that answered keeps its own reason; a preparation that threw has none to give.
      if (currentJournalCapability()?.durable !== false) {
        recordJournalCapability({
          durable: false,
          reason: 'preparation_failed',
        });
      }
      prepared(false);
      const journal = currentJournalCapability();
      logger.warn(
        'Agent harness unavailable: its store preparation failed; serving everything else and trying again',
        {
          error: errorText(failure.error),
          journal,
          detail: journal === null ? undefined : JOURNAL_DETAIL[journal.reason],
        },
      );
      backgroundPreparation?.abort();
      backgroundPreparation = new AbortController();
      void prepareInBackground(
        logger,
        capabilities,
        retry,
        { ...hooks, prepared },
        backgroundPreparation.signal,
      );
      return;
    }
    logger.warn(
      'Agent harness store unreachable; not serving until it answers',
      {
        error: errorText(failure.error),
        attempt,
      },
    );
    await retry.wait(attempt);
  }
}

/** Ends a background preparation (a stopping process). */
export function stopAgentHarnessStorePreparation(): void {
  backgroundPreparation?.abort();
  backgroundPreparation = null;
}

async function prepareInBackground(
  logger: Pick<Logger, 'info' | 'warn' | 'error'>,
  capabilities: HarnessCapabilityService,
  retry: StorePreparationRetry,
  hooks: Required<Pick<StorePreparationHooks, 'prepared'>> &
    StorePreparationHooks,
  signal: AbortSignal,
): Promise<void> {
  for (let attempt = 1; ; attempt += 1) {
    await retry.wait(attempt);
    if (signal.aborted) return;
    if (
      (await tryPrepareStore(logger, capabilities, hooks.migrations)) !== null
    ) {
      continue;
    }
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- the signal can abort while the preparation above is awaited
    if (signal.aborted) return;
    hooks.prepared(true);
    logger.info('Agent harness on: its store preparation succeeded', {
      attempts: attempt + 1,
    });
    hooks.whenPrepared?.();
    return;
  }
}

interface PreparationFailure {
  error: unknown;
  /** MongoDB is away for the whole application, not refusing the harness alone. */
  unreachable: boolean;
}

// A journal probe that could not tell: its majority write may have failed only because no primary answers.
const UNDETERMINED: ReadonlySet<JournalCapability['reason']> = new Set([
  'probe_failed',
  'majority_unavailable',
]);

/** Null when the store is prepared and its journal durable; otherwise why not. */
async function tryPrepareStore(
  logger: Pick<Logger, 'info' | 'warn' | 'error'>,
  capabilities: HarnessCapabilityService,
  migrations: readonly HarnessMigration[] | undefined,
): Promise<PreparationFailure | null> {
  try {
    await prepareStoreOnce(logger, capabilities, migrations);
  } catch (error) {
    return { error, unreachable: storeUnreachable(error) };
  }
  const journal = currentJournalCapability();
  if (journal === null || journal.durable) return null;
  return {
    error: new Error(`journal not durable: ${journal.reason}`),
    unreachable: UNDETERMINED.has(journal.reason) && !(await writablePrimary()),
  };
}

const errorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

const NOTHING_MIGRATED: HarnessStoreMigration = {
  droppedIndexes: [],
  createdIndexes: [],
  orphanedDispatchRows: 0,
};

async function prepareStoreOnce(
  logger: Pick<Logger, 'info' | 'warn' | 'error'>,
  capabilities: HarnessCapabilityService,
  migrations: readonly HarnessMigration[] | undefined,
): Promise<void> {
  const migration = await mongoBootMigration(NOTHING_MIGRATED, () =>
    migrateHarnessStore(undefined, migrations),
  );
  if (migration.droppedIndexes.length > 0) {
    logger.info('Agent harness dropped legacy run indexes', {
      indexes: migration.droppedIndexes,
    });
  }
  if (migration.createdIndexes.length > 0) {
    logger.info('Agent harness built indexes', {
      indexes: migration.createdIndexes,
    });
  }
  if (migration.orphanedDispatchRows > 0) {
    logger.warn('Agent harness ignores orphaned legacy dispatch rows', {
      collection: 'agentDispatchOutbox',
      rows: migration.orphanedDispatchRows,
    });
  }
  await capabilities.refresh();
}

const usesSandboxBroker = (
  env: Readonly<Record<string, string | undefined>>,
): boolean => (env.SANDBOX_MODE ?? '').trim().toLowerCase() === 'broker';

/**
 * `sandbox.isolation` from the broker (P5.11), only where `run_code` uses it
 * (`SANDBOX_MODE=broker`, the shipped default): elsewhere it keeps `none`.
 */
export function sandboxIsolationProbes(
  container: Container,
  dependencies: Pick<AgentHarnessDependencies, 'sandboxBrokerEndpoint'>,
  env: Readonly<Record<string, string | undefined>>,
): CapabilityProbe[] {
  const endpoint = dependencies.sandboxBrokerEndpoint;
  if (endpoint === undefined || !usesSandboxBroker(env)) return [];
  return [
    new HttpSandboxIsolationProbe(endpoint, () =>
      container
        .get<HarnessAuthority>(AGENT_HARNESS_AUTHORITY)
        .workerCredential(),
    ),
  ];
}

/**
 * Generates the worker credential and the attempt-token keyring on first
 * startup, and applies an operator rotation (`install.sh
 * --rotate-signing-secrets` sets ROTATE_SIGNING_SECRETS) once. Verification
 * never generates, so until this succeeds every claim is refused.
 *
 * With durable runs off but SANDBOX_MODE=broker (the Compose default), only
 * the worker credential is provisioned: the sandbox broker and the egress
 * proxy refuse every caller without it, so `run_code` would fail.
 */
const COMPONENT_REPUBLISH_MS = 5 * 60_000;

export async function prepareAgentHarnessAuthority(
  container: Container,
  logger: Pick<Logger, 'info' | 'error'>,
  env: Readonly<Record<string, string | undefined>> = process.env,
): Promise<void> {
  const harness = isAgentHarnessInfrastructureReady(env, null);
  if (!harness && !usesSandboxBroker(env)) return;
  const rotateId = env.ROTATE_SIGNING_SECRETS?.trim() || null;
  try {
    const authority = container.get<HarnessAuthority>(AGENT_HARNESS_AUTHORITY);
    if (harness) {
      await authority.provision(rotateId);
    } else {
      await authority.provisionWorkerCredential(rotateId);
    }
    // ADR-KEY-02: the sandbox-plane components' own documents, now and every few minutes (a Redis restart drops
    // the ACL users; a rotation elsewhere changes the digests).
    if (container.isBound(AGENT_HARNESS_COMPONENT_PUBLISHER)) {
      const publisher = container.get<ComponentCredentialPublisher>(
        AGENT_HARNESS_COMPONENT_PUBLISHER,
      );
      await publisher.publish();
      detachedInterval(() => void publisher.publish(), COMPONENT_REPUBLISH_MS);
    }
    // With the worker credential, harness on or off: the gateways that start under broker mode find published keys.
    if (container.isBound(AGENT_HARNESS_GATEWAY_TOKENS)) {
      await container
        .get<GatewayTokenService>(AGENT_HARNESS_GATEWAY_TOKENS)
        .provision(rotateId);
    }
    logger.info('Agent harness authority provisioned', {
      scope: harness ? 'harness' : 'sandbox_broker',
    });
  } catch (error) {
    logger.error('Agent harness authority provisioning failed', {
      error: error instanceof Error ? error.message : String(error),
    });
  }
}

/**
 * Dispatch delivery needs nothing here: admission and control write to the
 * shared outbox, which the application's OutboxDispatcher publishes. The
 * expiry sweeper and the audit anchorer run per process, each electing one
 * leader across replicas.
 */
const revocationReaders = new WeakMap<Container, ReleaseKeyRevocationReader>();
const capabilityRepairs = new WeakMap<Container, OrgCapabilityRepair>();

/**
 * ADR-OPS-08: both edges where an org's harness turns on catch its control-plane grants up, a Labs save (recorded
 * here as the listener) and this start for every org whose harness is on.
 */
function startCapabilityRepair(container: Container): void {
  if (!container.isBound(CONTROL_PLANE_CAPABILITY_REPAIR)) return;
  const repair = container.get<OrgCapabilityRepair>(
    CONTROL_PLANE_CAPABILITY_REPAIR,
  );
  recordOrgHarnessTurnedOn((orgId) => {
    void repair.request(orgId);
  });
  repair.start();
  capabilityRepairs.set(container, repair);
}

export function startAgentHarnessBackgroundServices(
  container: Container,
): void {
  if (!isAgentHarnessInfrastructureReady()) return;
  if (container.isBound(AGENT_HARNESS_RUN_SWEEPER)) return;
  startChangeNotifier(container);
  // R41SUPPLY-FU-6: a release key the deployment's stored list revokes verifies no vendor rules here.
  if (container.isBound('KeyValueStoreService')) {
    const reader = new ReleaseKeyRevocationReader(
      container.get<KeyValueStoreService>('KeyValueStoreService'),
      container.isBound('Logger') ? container.get<Logger>('Logger') : undefined,
    );
    reader.start();
    revocationReaders.set(container, reader);
  }
  if (container.isBound(AGENT_HARNESS_CAPABILITIES)) {
    const capabilities = container.get<HarnessCapabilityService>(
      AGENT_HARNESS_CAPABILITIES,
    );
    capabilities.start();
    recordCapabilitySettlement(() => capabilities.settled());
  }
  startCapabilityRepair(container);
  const sweeper = new RunSweeper({
    executions: container.get<ExecutionService>(
      AGENT_HARNESS_EXECUTION_SERVICE,
    ),
    lease: createLeaderLease(),
    liveness: createControlPlaneLiveness(),
    logger: container.get<Logger>('Logger'),
    holderId: `${hostname()}:${String(process.pid)}:${randomUUID()}`,
    reaper: container.isBound(AGENT_HARNESS_SANDBOX_REAPER)
      ? container.get<SandboxReaper>(AGENT_HARNESS_SANDBOX_REAPER)
      : null,
    pendingReaps: createPendingReaps(),
    nextRuns: container.isBound(AGENT_HARNESS_NEXT_RUNS)
      ? container.get<NextRunService>(AGENT_HARNESS_NEXT_RUNS)
      : null,
    memories: container.isBound(AGENT_HARNESS_MEMORIES)
      ? container.get<MemoryService>(AGENT_HARNESS_MEMORIES)
      : null,
    approverNotices: container.isBound(AGENT_HARNESS_APPROVER_LINK_NOTICES)
      ? container.get<WaitingRunNotices>(AGENT_HARNESS_APPROVER_LINK_NOTICES)
      : null,
    operatorControls: container.isBound(AGENT_HARNESS_OPERATOR_CONTROLS)
      ? container.get<OperatorStops>(AGENT_HARNESS_OPERATOR_CONTROLS)
      : null,
  });
  sweeper.start();
  container
    .bind<RunSweeper>(AGENT_HARNESS_RUN_SWEEPER)
    .toConstantValue(sweeper);
  if (container.isBound(AGENT_HARNESS_DATA_KEYS)) {
    const journal = container.get<HarnessJournal>(AGENT_HARNESS_DATA_KEYS);
    sealRunInputsQuietly(journal.runInput, container.get<Logger>('Logger'));
    const anchoring = new AuditAnchorService({
      chain: journal.audit,
      sink: journal.anchors,
      lease: createLeaderLease(),
      holderId: `${hostname()}:${String(process.pid)}:${randomUUID()}`,
      logger: container.get<Logger>('Logger'),
    });
    anchoring.start();
    container
      .bind<AuditAnchorService>(AGENT_HARNESS_AUDIT_ANCHORING)
      .toConstantValue(anchoring);
    const linkWatch = new AuditLinkWatch({
      chain: journal.audit,
      metrics: prometheusAuditLinkMetrics,
      logger: container.get<Logger>('Logger'),
    });
    linkWatch.start();
    container
      .bind<AuditLinkWatch>(AGENT_HARNESS_AUDIT_LINK_WATCH)
      .toConstantValue(linkWatch);
    startEditionWorkers(container);
  }
}

/** Seals the plaintext queries stored before LCFIX and drops `/review` diffs from messages (LC2FIX); a failure is logged and the next boot resumes it. */
function sealRunInputsQuietly(
  seal: RunInputSeal,
  logger: Pick<Logger, 'info' | 'error'>,
): void {
  mongoBootMigration<[RunInputMigration, number]>([{}, 0], (db) =>
    Promise.all([sealStoredRunInputs(seal, db()), typedReviewMessages(db())]),
  ).then(
    ([sealed, reviews]) => {
      if (Object.values(sealed).some((count) => count > 0) || reviews > 0) {
        logger.info('Agent harness sealed stored run inputs', {
          sealed,
          reviewMessages: reviews,
        });
      }
    },
    (error: unknown) => {
      logger.error('Agent harness could not seal stored run inputs', {
        error: errorText(error),
      });
    },
  );
}

/**
 * Session erasure runs whether or not the harness is enabled: deleting a
 * conversation erases its messages, citations and artifacts either way (X-5).
 * Its indexes are built here, not by `prepareAgentHarnessStore`, which a
 * flag-off install skips; without the unique job index a session could get two
 * erasure jobs, so the workers stay stopped when the build fails.
 */
export async function startSessionErasureServices(
  container: Container,
  logger: Pick<Logger, 'info' | 'error'>,
  migrate: () => Promise<{ created: string[] }> = () =>
    mongoBootMigration({ created: [], dropped: [] }, (db) =>
      migrateErasureStore(db()),
    ),
): Promise<void> {
  if (
    !container.isBound(AGENT_HARNESS_ERASURE_WORKER) &&
    !container.isBound(AGENT_HARNESS_RETENTION_SWEEPER)
  ) {
    return;
  }
  try {
    const { created } = await migrate();
    if (created.length > 0) {
      logger.info('Session erasure built indexes', { indexes: created });
    }
  } catch (error) {
    logger.error(
      'Session erasure store preparation failed; erasure stays stopped',
      {
        error: error instanceof Error ? error.message : String(error),
      },
    );
    return;
  }
  if (container.isBound(AGENT_HARNESS_ERASURE_WORKER)) {
    container
      .get<SessionErasureWorker<StoreSession>>(AGENT_HARNESS_ERASURE_WORKER)
      .start();
  }
  if (container.isBound(AGENT_HARNESS_RETENTION_SWEEPER)) {
    container
      .get<RetentionSweeper<StoreSession>>(AGENT_HARNESS_RETENTION_SWEEPER)
      .start();
  }
}

/**
 * Scheduling runs whether or not the harness is on (ADR-SCH-02 D1): the firer starts with the app on every install,
 * after the indexes its exactly-once claim relies on (a firing's unique slot) are built here, since
 * `prepareAgentHarnessStore` is skipped while the harness switch is off. A failed build leaves the firer stopped.
 */
export async function startScheduleServices(
  container: Container,
  logger: Pick<Logger, 'info' | 'error'>,
  migrate: () => Promise<{ created: string[] }> = () =>
    mongoBootMigration({ created: [], dropped: [] }, (db) =>
      migrateScheduleStore(db()),
    ),
  storeReady: () => boolean = () => transactionsAvailable(),
): Promise<void> {
  if (!container.isBound(AGENT_HARNESS_SCHEDULE_FIRER)) return;
  if (!storeReady()) {
    logger.error(
      'Schedules stay stopped: their store needs transactions (a MongoDB replica set, or PostgreSQL); a create answers SCHEDULED_RUNS_STORE_UNAVAILABLE',
      { replicaSet: false },
    );
    return;
  }
  try {
    const { created } = await migrate();
    if (created.length > 0) {
      logger.info('Schedules built indexes', { indexes: created });
    }
    container.get<ScheduleFirer>(AGENT_HARNESS_SCHEDULE_FIRER).start();
  } catch (error) {
    logger.error('Schedule store preparation failed; schedules stay stopped', {
      error: error instanceof Error ? error.message : String(error),
    });
  }
}

/** Where agent deletion records its erasure intent and queues a failed erasure, for the retention sweeper (H4, H5). */
export function agentErasureQueue(
  container: Container,
): AgentErasureQueue | null {
  return container.isBound(AGENT_HARNESS_AGENT_ERASURES)
    ? container.get<AgentErasureQueue>(AGENT_HARNESS_AGENT_ERASURES)
    : null;
}

/** G-15: where the conversation module's feedback routes link a rating to its run; absent: not linked. */
export function runFeedback(
  container: Container,
): RunFeedbackService | undefined {
  return container.isBound(AGENT_HARNESS_RUN_FEEDBACK)
    ? container.get<RunFeedbackService>(AGENT_HARNESS_RUN_FEEDBACK)
    : undefined;
}

/**
 * Where the conversation module's delete routes enqueue an erasure, in their own transaction. Undefined where no
 * delete on this install erases (`erasureMayRun`); otherwise each delete asks `erasureOnDelete` for its org and,
 * where that says no, soft-deletes the chat as a release without E7 did (`byOrgErasure`).
 */
export function sessionErasures(
  container: Container,
  env: NodeJS.ProcessEnv = process.env,
): SessionErasureQueue<StoreSession> | undefined {
  if (!erasureMayRun(env)) return undefined;
  return container.isBound(AGENT_HARNESS_SESSION_ERASURES)
    ? container.get<SessionErasureQueue<StoreSession>>(
        AGENT_HARNESS_SESSION_ERASURES,
      )
    : undefined;
}

/**
 * A delete route built once with the erasure queue and once without it; each request takes the one its org's
 * `erasureOnDelete` names (ADR-OPS-08), so a chat of an org with the harness off is soft-deleted as before.
 */
export function byOrgErasure<Q>(
  queue: Q | undefined,
  build: (queue: Q | undefined) => RequestHandler,
  env: NodeJS.ProcessEnv = process.env,
): RequestHandler {
  const soft = build(undefined);
  if (queue === undefined) return soft;
  const erasing = build(queue);
  return (req, res, next) => {
    const orgId = (req as Request & { user?: { orgId?: unknown } }).user?.orgId;
    erasureOnDelete(typeof orgId === 'string' ? orgId : null, env).then(
      (erases) => (erases ? erasing : soft)(req, res, next),
      next,
    );
  };
}

/** What the conversation module's delete routes call, once the delete commits, to stop the chat's run (R30-LAT). */
export function sessionRunStopper(
  container: Container,
): SessionRunStopperPort | undefined {
  return container.isBound(AGENT_HARNESS_SESSION_RUN_STOPPER)
    ? container.get<SessionRunStopperPort>(AGENT_HARNESS_SESSION_RUN_STOPPER)
    : undefined;
}

const backgroundStops = new WeakMap<Container, Promise<void>>();

/**
 * The run sweeper first: runs wait on its leadership, so it changes hands
 * before the slower stops below. Runs once per container: a later call (the
 * disposal after `quiesce`) waits for it instead of releasing every lease again.
 */
export function stopAgentHarnessBackgroundServices(
  container: Container,
): Promise<void> {
  let stopped = backgroundStops.get(container);
  if (!stopped) {
    stopped = stopBackgroundServicesOnce(container);
    backgroundStops.set(container, stopped);
  }
  return stopped;
}

async function stopBackgroundServicesOnce(container: Container): Promise<void> {
  stopAgentHarnessStorePreparation();
  revocationReaders.get(container)?.stop();
  const repair = capabilityRepairs.get(container);
  if (repair !== undefined) {
    recordOrgHarnessTurnedOn(null);
    await repair.stop();
  }
  if (container.isBound(AGENT_HARNESS_RUN_SWEEPER)) {
    await container.get<RunSweeper>(AGENT_HARNESS_RUN_SWEEPER).stop();
  }
  if (container.isBound(AGENT_HARNESS_ERASURE_WORKER)) {
    await container
      .get<SessionErasureWorker<StoreSession>>(AGENT_HARNESS_ERASURE_WORKER)
      .stop();
  }
  if (container.isBound(AGENT_HARNESS_RETENTION_SWEEPER)) {
    await container
      .get<RetentionSweeper<StoreSession>>(AGENT_HARNESS_RETENTION_SWEEPER)
      .stop();
  }
  if (container.isBound(AGENT_HARNESS_CAPABILITIES)) {
    container.get<HarnessCapabilityService>(AGENT_HARNESS_CAPABILITIES).stop();
    recordCapabilitySettlement(null);
  }
  if (container.isBound(AGENT_HARNESS_AUDIT_ANCHORING)) {
    await container
      .get<AuditAnchorService>(AGENT_HARNESS_AUDIT_ANCHORING)
      .stop();
  }
  if (container.isBound(AGENT_HARNESS_AUDIT_LINK_WATCH)) {
    await container.get<AuditLinkWatch>(AGENT_HARNESS_AUDIT_LINK_WATCH).stop();
  }
  await stopEditionWorkers(container);
  if (container.isBound(AGENT_HARNESS_SCHEDULE_FIRER)) {
    await container.get<ScheduleFirer>(AGENT_HARNESS_SCHEDULE_FIRER).stop();
  }
  await stopChangeNotifier(container);
  // A link under way when the process stops finishes, so a stop leaves the chain whole.
  await auditLinksIdle();
}

/**
 * `conversation.activeRunId` on a conversation GET (REPORT 8), for the
 * routes the conversation module owns; a no-op while the harness is off.
 */
export function createActiveRunMiddleware(
  container: Container,
  kind: 'agent' | 'chat',
): RequestHandler {
  const disabled: RequestHandler = (_req, _res, next) => {
    next();
  };
  if (!container.isBound(AGENT_HARNESS_SESSION_RUN_SERVICE)) return disabled;
  return activeRunIdMiddleware(
    container.get<SessionRunService>(AGENT_HARNESS_SESSION_RUN_SERVICE),
    kind,
    () => isAgentHarnessInfrastructureReady(),
    container.isBound('Logger') ? container.get<Logger>('Logger') : undefined,
  );
}

/**
 * ADR-UI-01 D2: each conversation of the caller's list with its run state; a no-op while the harness is off or the
 * run lists are not bound.
 */
export function createConversationRunStatesMiddleware(
  container: Container,
): RequestHandler {
  return conversationRunStatesMiddleware(
    () =>
      container.isBound(AGENT_HARNESS_RUN_LISTS)
        ? container.get<RunListService>(AGENT_HARNESS_RUN_LISTS)
        : null,
    () => isAgentHarnessInfrastructureReady(),
    container.isBound('Logger') ? container.get<Logger>('Logger') : undefined,
  );
}

/**
 * A chat turn on an existing chat route (LAT1: web, API/PAT, MCP; P1b C-5:
 * Slack) in front of the legacy stream handler the conversation module owns;
 * a no-op while the harness is off or not fully bound.
 */
export function createChatTurnMiddleware(
  container: Container,
  options: Omit<ChatTurnRouteOptions, 'enabled'>,
): RequestHandler {
  const service = chatTurnService(container);
  if (service === null) return passThrough;
  const modelGroups = chatModelGroupsOf(container);
  return chatTurnMiddleware(service, {
    ...options,
    enabled: () => isAgentHarnessInfrastructureReady(),
    ...(modelGroups !== undefined ? { modelGroups } : {}),
    ...(container.isBound(AGENT_HARNESS_RUN_WATCHERS)
      ? {
          watchers: container.get<RunWatchRegistry>(AGENT_HARNESS_RUN_WATCHERS),
        }
      : {}),
  });
}

/**
 * The existing cancel routes in front of today's cancel (LAT2): a run id
 * naming the conversation's running durable turn stops it; a no-op while the
 * harness is off or not fully bound.
 */
export function createChatCancelMiddleware(
  container: Container,
  options: Omit<ChatCancelRouteOptions, 'enabled'>,
): RequestHandler {
  const service = chatTurnService(container);
  if (service === null) return passThrough;
  return chatCancelMiddleware(service, {
    ...options,
    enabled: () => isAgentHarnessInfrastructureReady(),
  });
}

const passThrough: RequestHandler = (_req, _res, next) => {
  next();
};

function chatTurnService(container: Container): ChatTurnService | null {
  if (
    !container.isBound(AGENT_HARNESS_RUN_ADMISSION_SERVICE) ||
    !container.isBound(AGENT_HARNESS_SESSION_RUN_SERVICE) ||
    !container.isBound(AGENT_HARNESS_RUN_STREAMS)
  ) {
    return null;
  }
  return new ChatTurnService({
    admissions: container.get<RunAdmissionService>(
      AGENT_HARNESS_RUN_ADMISSION_SERVICE,
    ),
    runs: container.get<SessionRunService>(AGENT_HARNESS_SESSION_RUN_SERVICE),
    streams: container.get<RunStreamService>(AGENT_HARNESS_RUN_STREAMS),
    logger: container.isBound('Logger')
      ? container.get<Logger>('Logger')
      : undefined,
    runKeys: container.isBound(AGENT_HARNESS_RUN_KEYS)
      ? container.get<RunKeyDigest>(AGENT_HARNESS_RUN_KEYS)
      : undefined,
    waitingOn: container.isBound(AGENT_HARNESS_RUN_TIMELINES)
      ? pendingCalls(
          container.get<RunTimelineService>(AGENT_HARNESS_RUN_TIMELINES),
        )
      : undefined,
  });
}

/** The calls a run's open cards hold, each tool once, as the projector's hand-off names them. */
const pendingCalls =
  (timelines: Pick<RunTimelineService, 'approvals'>): WaitingOnReader =>
  async (run) => {
    const out: Array<{ toolName: string; displayName?: string }> = [];
    for (const approval of await timelines.approvals(run)) {
      if (
        approval.status !== 'pending' ||
        out.some((a) => a.toolName === approval.toolName)
      )
        continue;
      out.push({
        toolName: approval.toolName,
        ...(approval.displayName !== undefined
          ? { displayName: approval.displayName }
          : {}),
      });
    }
    return out;
  };

/**
 * The citation check of shared conversation reads (C-6), for the routes the
 * conversation module owns. Unbound: shared reads hide record citations.
 */
export function sharedCitations(
  container: Container,
): SharedCitationService | undefined {
  return container.isBound(AGENT_HARNESS_SHARED_CITATIONS)
    ? container.get<SharedCitationService>(AGENT_HARNESS_SHARED_CITATIONS)
    : undefined;
}

/**
 * The artifact check of shared conversation reads (SS0), for the routes the
 * conversation module owns. Unbound: shared reads hide every artifact.
 */
export function sharedArtifacts(
  container: Container,
): SharedArtifactService | undefined {
  return container.isBound(AGENT_HARNESS_SHARED_ARTIFACTS)
    ? container.get<SharedArtifactService>(AGENT_HARNESS_SHARED_ARTIFACTS)
    : undefined;
}

/**
 * Who records a change to a conversation's participants (SS1), for the share
 * routes the conversation module owns: asked per request, so it is absent
 * wherever the harness is off for the request's org (ADR-OPS-08) and those
 * routes behave as before.
 */
export function sessionParticipants(
  container: Container,
): () => Promise<SessionParticipantRecorder<StoreSession> | undefined> {
  if (!container.isBound(AGENT_HARNESS_SESSION_PARTICIPANTS)) {
    return () => Promise.resolve(undefined);
  }
  const recorder = container.get<SessionParticipantRecorder<StoreSession>>(
    AGENT_HARNESS_SESSION_PARTICIPANTS,
  );
  return async () =>
    (await orgHarnessOn(getCurrentOrgId())) ? recorder : undefined;
}

/** The fork service for the conversation routes (SS3), only where the harness is on for the request's org. */
export function sessionForks(
  container: Container,
): () => Promise<SessionForkService<StoreSession> | undefined> {
  if (!container.isBound(AGENT_HARNESS_SESSION_FORKS)) {
    return () => Promise.resolve(undefined);
  }
  return async () =>
    (await orgHarnessOn(getCurrentOrgId()))
      ? container.get<SessionForkService<StoreSession>>(
          AGENT_HARNESS_SESSION_FORKS,
        )
      : undefined;
}
