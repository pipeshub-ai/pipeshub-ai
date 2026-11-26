/**
 * The control plane's public barrel (docs/control-plane/03-interfaces.md §1):
 * other modules import the control plane only from here.
 */
export {
  ERROR_CODES,
  LAYERS,
  REMEDIATION_ACTORS,
  problemSchema,
  type ErrorCode,
  type LayerName,
  type Problem,
  type RemediationActor,
} from './contracts/generated';
export {
  createBindingService,
  CONTROL_PLANE_MODULES,
  createExtensionRegistry,
} from './composition';
export {
  controlPlaneAdminBindings,
  type ControlPlaneAdminOptions,
} from './composition';
export {
  CONTROL_PLANE_ROUTES,
  createControlPlaneAdminRouter,
} from './transport/admin-api';
export {
  CONTROL_PLANE_API_PREFIX,
  createCpRouter,
  registerCpRoutes,
  cpRoute,
  parseRevisionHeader,
  refusalOf,
  type AnyCpRouteSpec,
  type CpRouteSpec,
} from './transport/cp-route';
export * from './transport/admin-keys';
export {
  GrantAdmin,
  narrows as grantNarrows,
  type AdminPinServices,
  type GrantableItem,
} from './application/grant-admin';
export {
  allowedVisibility,
  InstallationAdmin,
  visibleTo,
} from './application/installation-admin';
export type {
  Authorizer,
  AuthzActor,
  AuthzDecision,
  CpAction,
  ResourceRef,
} from './ports/authorizer';
export { ControlPlaneUserEraser } from './application/user-data-eraser';
export { CpProblemError, CpRefusal, notFound } from './domain/errors';
export type { BindingService } from './application/binding-service';
export type { BindingServiceDependencies } from './composition';
export {
  CONTROL_PLANE_BINDINGS,
  CONTROL_PLANE_INTERNAL_BINDINGS_PREFIX,
  createInternalBindingRouter,
} from './transport/binding-routes';
export {
  CONTROL_PLANE_ME_CAPABILITIES,
  CONTROL_PLANE_ME_CAPABILITIES_PATH,
  createMeCapabilitiesRouter,
  type MeCapabilitiesSource,
  type TurnRoute,
  type TurnRoutes,
} from './transport/me-capabilities-routes';
export {
  buildReleaseCatalog,
  listedReleaseNames,
} from './application/release-catalog';
export {
  readProblem,
  type ProblemReading,
  type ProblemRefusal,
} from './domain/problem';
export type { CatalogItem } from './domain/catalog-item';
export type { CatalogStore } from './ports/catalog-store';
export type { DomainModule, CatalogSource } from './ports/extension-points';
export {
  actorRefOfUser,
  changedPaths,
  changeEventOf,
  CONFIG_CHANGED_KIND,
  configStreamId,
  CONTROL_PLANE_WRITER,
  orgScope,
  summarizePaths,
  userScope,
} from './domain/config-change';
export type {
  ActorRef,
  ChangeEvent,
  ConfigChange,
  ConfigChanged,
  ConfigObjectRef,
  ScopeRef,
} from './domain/config-change';
export {
  ControlPlaneError,
  ControlPlaneRetryableError,
  ControlPlaneRevisionConflictError,
  ControlPlaneUnavailableError,
} from './domain/errors';
export type {
  ChangeOutbox,
  ConfigAuditLog,
  ConfigAuditReader,
} from './ports/config-audit-log';
export type { ControlPlaneLog } from './ports/log';
export type {
  Applied,
  Committed,
  ControlPlaneTransaction,
  CpTx,
  WriteContext,
  Work,
} from './ports/transaction';
export { MongoControlPlaneTransaction } from '../../libs/repositories/control-plane/control-plane-transaction/mongo';
export { createControlPlaneWriteTransaction } from '../../libs/repositories/control-plane/control-plane-transaction';
export { MongoChangeOutbox } from '../../libs/repositories/control-plane/change-outbox/mongo';
export { createChangeOutbox } from '../../libs/repositories/control-plane/change-outbox';
export { BrokerChangeNotifier } from './adapters/broker-change-notifier';
export type { ChangeNotifier, Unsubscribe } from './ports/change-notifier';
export { ChangeSubscribers } from './domain/change-subscribers';
export {
  CHANGE_EVENT_TYPE,
  changeEventMessage,
  CONTROL_PLANE_EVENTS_TOPIC,
} from './domain/change-message';
export { liveLayerOf, type LiveLayerInput } from './domain/live-layer';
export { liveLayerSchema, type LiveLayer } from './contracts/generated';
export { resolvedTurnSchema, type ResolvedTurn } from './contracts/generated';
export { MongoOrgHeadLeases } from '../../libs/repositories/control-plane/org-heads/mongo';
export {
  ControlPlaneRepair,
  OrderedStepsTransaction,
} from './adapters/ordered-steps-transaction';
export type {
  AcquiredLease,
  OrgHeadLeases,
} from './adapters/ordered-steps-transaction';
export type { RetryPolicy } from './adapters/staged-transaction';
export { controlPlaneHttpError } from './adapters/http-error';
export type { RunConfigPinner } from './ports/run-config-pinner';
export type {
  PinBasis,
  PinResult,
  PinSubject,
  ResolvedRunConfig,
  RouteResult,
  RouteSubject,
} from './domain/resolution';
export {
  assistantAgentLoop,
  classicAllowed,
  familyOf,
  LEGACY_REASONS,
  NO_FACTS,
  NO_FAMILIES,
  routingOf,
  type ConfiguredFamilies,
  type LegacyReason,
  type RoutedTurn,
  type Routing,
  type RoutingFacts,
} from './domain/routing';
export {
  CAPABILITY_MIGRATION_ID,
  CapabilityMigration,
} from './application/capability-migration';
export type { CapabilityMigrationDeps } from './application/capability-migration';
export type {
  CapabilityRepair,
  CapabilitySync,
  OrgReport,
} from './ports/capability-sync';
export { loadCapabilityFacts } from './application/capability-facts';
export type {
  CapabilityFacts,
  CapabilityStores,
} from './application/capability-facts';
export { EffectiveSetShadow } from './application/effective-set-shadow';
export type {
  ShadowMismatchCounter,
  ShadowReview,
  ShadowSubject,
} from './application/effective-set-shadow';
export type { LegacyPin, ShadowDimension } from './domain/shadow-diff';
export { CanonicalEffectiveSetResolver } from './adapters/effective-set-resolver';
export {
  CAPABILITY_SYNC_PATH,
  createCapabilitySyncRouter,
} from './adapters/http/capability-sync-router';
export {
  HttpLegacyCapabilitySource,
  LegacySourceUnavailableError,
} from './adapters/http/legacy-capability-source';
export type { LegacyCapabilitySourceConfig } from './adapters/http/legacy-capability-source';
export { HttpMembershipPort } from './adapters/http/membership-client';
export { INSTALLATIONS_MIGRATION } from '../../libs/repositories/control-plane/migrations/mongo';
export { MongoCapabilityLedger } from '../../libs/repositories/control-plane/capability-ledger/mongo';
export { createCapabilityLedger } from '../../libs/repositories/control-plane/capability-ledger';
export { MongoBindingStore } from '../../libs/repositories/control-plane/bindings/mongo';
export { createBindingStore } from '../../libs/repositories/control-plane/bindings';
export { MongoGrantStore } from '../../libs/repositories/control-plane/grant-store/mongo';
export { createGrantStore } from '../../libs/repositories/control-plane/grant-store';
export { MongoInstallationStore } from '../../libs/repositories/control-plane/installations/mongo';
export { createInstallationStore } from '../../libs/repositories/control-plane/installations';
export { releaseItemsOf } from './domain/release-items';
export { skillInstallationId } from './application/capability-plan';
export {
  countShadowMismatch,
  prometheusShadowMismatches,
} from './adapters/telemetry/shadow-metrics';
export type {
  LayerRead,
  LayerWrite,
  LegacyLayers,
  LegacyPolicySource,
  PolicyLayers,
  PolicyOrgSource,
  PolicyRevision,
  PolicyScopeIndex,
  PolicyStore,
  VersionedLayer,
} from './ports/policy-store';
export type { PolicyLayer, PolicyRefusal } from './domain/policy-alias';
export { readPolicyLayer } from './domain/policy-layer';
export {
  observePolicyWrites,
  policyWritesObserved,
  policyWritten,
  type PolicyWriteObserver,
} from './ports/policy-write-observers';
export {
  ControlPlaneStoreError,
  InvalidObject,
  LegacyPolicyUnreadable,
} from './domain/store-errors';
export type {
  ControlPlaneDataMigration,
  MigrationLedger,
  MigrationMode,
} from './ports/data-migration';
export { MongoPolicyStore } from '../../libs/repositories/control-plane/policy/mongo';
export { createPolicyStore } from '../../libs/repositories/control-plane/policy';
export { PgPolicyStore } from '../../libs/repositories/control-plane/policy/pg';
export { MongoMigrationLedger } from '../../libs/repositories/control-plane/migration-ledger/mongo';
export { createMigrationLedger } from '../../libs/repositories/control-plane/migration-ledger';
export { CONTROL_PLANE_MIGRATIONS } from '../../libs/repositories/control-plane/migrations/mongo';
export { CONTROL_PLANE_DATA_MIGRATIONS } from './application/data-migrations';
export {
  DEPLOYMENT_FLOW_SCOPE,
  DEPLOYMENT_SCOPE,
  deploymentAgentScope,
  layerSource,
  parentLayer,
  prefixSources,
  runSources,
  inheritedDimensions,
  type ParentDimensions,
  type ModelSourceOptions,
} from './application/layer-sources';
export { allowedRoutes } from './application/model-sources';
export {
  assemblePin,
  basisHeads,
  pinnedAgentOwner,
  pinScope,
  sealPin,
  type PinFields,
} from './application/pin';
export {
  PolicyRunConfigPinner,
  type PolicyRunConfigPinnerOptions,
} from './application/run-config-pinner';
export {
  ResolvedSnapshotCache,
  type SnapshotCacheOptions,
  type SnapshotLoader,
} from './application/snapshot-cache';
export {
  DEPLOYMENT_HEAD_ID,
  MongoDeploymentRevision,
  MongoHeadRevisions,
} from '../../libs/repositories/control-plane/head-revisions/mongo';
export {
  PgDeploymentRevision,
  PgHeadRevisions,
} from '../../libs/repositories/control-plane/head-revisions/pg';
export {
  createDeploymentRevision,
  createHeadRevisions,
} from '../../libs/repositories/control-plane/head-revisions';
export type {
  DeploymentRevision,
  HeadRevisions,
  Heads,
} from './ports/head-revisions';
export { configuredRoutes, llmRoute, selectLlm } from './domain/llm';
export type { ConfiguredLlm, ModelChoice, SelectedLlm } from './domain/llm';
export { touchKey } from './domain/revisions';
export type { SnapshotSource } from './ports/snapshot-source';
export type { EffectiveSetResolver } from './ports/effective-set-resolver';
export type {
  EffectiveRunConfig,
  PolicyEngine,
  Folded,
} from './domain/policy-alias';
export type { Snapshot } from './domain/resolution';
export {
  CONTROL_PLANE_PIN_ENV,
  CONTROL_PLANE_PIN_GATES,
  InvalidControlPlanePinError,
  parseControlPlanePin,
} from './application/control-plane-pin';
export type { ControlPlanePin } from './application/control-plane-pin';
export type {
  ApprovalOutcome,
  ConfigChangeApprovals,
  ConfigChangeApprovalsFactory,
  DecideApproval,
  SubmitApproval,
} from './ports/config-approvals';
export {
  ApprovalForbiddenError,
  ApprovalInvalidError,
  ApprovalSupersededError,
} from './domain/approval-errors';
export { SkillCandidateApprovalTarget } from './modules/skills/candidate-approval-target';
export { candidateTargetId } from './domain/approval-targets';
export type { CandidateEffects } from './modules/skills/candidate-approval-target';
export type { ApprovalTargetHandler } from './ports/approval-targets';
export type { ApprovalRequest } from './domain/objects';
export type { AgentDirectory, MembershipPort } from './ports/directory';
export {
  CachingAgentDirectory,
  CachingMembershipPort,
  DIRECTORY_TTL_MS,
} from './application/directory-cache';
export { DirectoryEventsSubscriber } from './adapters/messaging/directory-events-subscriber';
export { silentControlPlaneLog } from './ports/log';
export { administers } from './domain/team-scope';
export {
  BUILT_IN_LAYER_MEMBERS,
  EDITION_DIMENSIONS,
  type LayerAbove,
  type PolicySectionRule,
} from './domain/section-rules';
export {
  WEBSITES_SECTION,
  type AllowedWebsite,
  type WebsiteAccess,
  type WebsitePolicy,
} from './domain/websites';
export {
  REPOSITORIES_SECTION,
  type AllowedRepositoryHost,
  type RepositoryPolicy,
} from './domain/repositories';
export {
  ACCESS_SECTION,
  type AccessGroupsPolicy,
} from './domain/access-groups';
export {
  APPROVER_LEVELS,
  approvalRequestSchema,
  policyLayerSchema,
  type ApproverLevel,
} from './contracts/generated';
export type {
  ApprovalDecision,
  ApprovalState,
  ApprovalTarget,
  ChangeKind,
  Installation,
} from './domain/objects';
export type { Page, StoreTx } from './ports/store-tx';
export type { InstallationStore } from './ports/installation-store';
export type { GroupMembership, OrgRoles } from './ports/org-roles';
export type {
  ApprovalTargetState,
  ApprovalVerdict,
} from './ports/approval-targets';
export { ApprovalTargetMoved } from './domain/approval-errors';
export {
  ApprovalStateConflict,
  ObjectExists,
  ObjectNotFound,
  WrongOrg,
} from './domain/store-errors';
export { NO_DELEGATION, RoleAuthorizer } from './application/role-authorizer';
export { UnreadableDirectory } from './adapters/unreadable-directory';
export {
  dimensionValue,
  scoped,
  type PolicyResolver,
} from './domain/policy-alias';
export { descriptorSetDigestInput } from './domain/digests';
export { teamOf, teamScope } from './domain/team-scope';
export type { CpRequestContext } from './transport/cp-route';
export { REFUSAL_CODES } from './domain/refusals';
