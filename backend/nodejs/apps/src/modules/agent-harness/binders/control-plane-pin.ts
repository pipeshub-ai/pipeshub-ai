import { envFlag } from '../../../libs/utils/env-flags';
import { groupAccessOf } from './group-access';
import {
  CachingAgentDirectory,
  CachingMembershipPort,
  CONTROL_PLANE_MEMBERSHIP,
  DIRECTORY_TTL_MS,
  HttpMembershipPort,
  type LegacyCapabilitySourceConfig,
  type MembershipPort,
  CanonicalEffectiveSetResolver,
  CONTROL_PLANE_PIN_GATES,
  CONTROL_PLANE_PIN_SERVICES,
  type AdminPinServices,
  createBindingStore,
  createCapabilityLedger,
  createGrantStore,
  createHeadRevisions,
  createInstallationStore,
  parseControlPlanePin,
  PolicyRunConfigPinner,
  prefixSources,
  ResolvedSnapshotCache,
  runSources,
} from '../../control-plane';
import { controlPlaneSubscribers } from '../adapters/control-plane/process-changes';
import { workerApprovals } from '../adapters/control-plane/org-policy-layers';
import { mergeOrgLayers, orgLayerSources } from './org-layer-sources';
import {
  policySnapshotLoader,
  type OrgLayers,
} from '../adapters/control-plane/policy-snapshot-loader';
import { prometheusPinShadowMetrics } from '../adapters/telemetry/pin-shadow-metrics';
import { ChildPinning } from '../application/admission/child-pinning';
import { LegacyTurnHeader } from '../application/legacy-turn-header';
import { LegacyTurnResolver } from '../application/legacy-turn-resolver';
import type { ResolvedTurnAuthority } from '../application/resolved-turn-authority';
import { holdsUnreadableLayer } from '../domain/resolved-turn';
import { harnessEditionOf } from '../edition';
import {
  OrgScopedRoutingDecider,
  ResolvedRoutingDecider,
  ShadowRoutingDecider,
} from '../application/resolved-routing';
import { RoutingFactsReader } from '../application/routing-facts';
import {
  PolicyPinning,
  type PinWorkerFeatures,
} from '../application/admission/policy-pinning';
import type { WorkerFeatureAvailability } from '../application/worker-feature-availability';
import {
  flowSettings,
  isAgentHarnessInfrastructureReady,
  orgHarnessOn,
} from '../config';
import type { FlowRule } from '../domain/approvals';
import { deploymentFlow, deploymentLayers } from '../domain/deployment-policy';
import type { RunAdmissionServiceOptions } from '../application/run-admission-service';
import {
  harnessLogMeta,
  silentHarnessLogger,
} from '../application/harness-log';
import { roleCostBound } from '../domain/child-model';
import { usdPerMTok } from '../domain/model-prices-release';
import { releaseLayers } from '../domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../domain/policy';
import type { AgentHarnessDependencies } from '../composition';
import type { RoutingDecider } from '../ports/routing-decider';
import { boundOrNull, HarnessBinder } from './context';
import { policyStoreIsRecord } from './policy-store';
import {
  AGENT_HARNESS_LEGACY_TURN_HEADER,
  AGENT_HARNESS_RESOLVED_TURNS,
  AGENT_HARNESS_WORKER_FEATURES,
  CONTROL_PLANE_AGENT_DIRECTORY,
} from './tokens';
import type { Container, interfaces } from 'inversify';

export const AGENT_HARNESS_POLICY_PINNING = 'AgentHarnessPolicyPinning';
export const AGENT_HARNESS_CHILD_PINNING = 'AgentHarnessChildPinning';
/** What every pin of this process reads: CPD-7's effective-set shadow compares on the same snapshots. */
export const AGENT_HARNESS_PIN_SNAPSHOTS = 'AgentHarnessPinSnapshots';
/** The route the resolved run configuration gives a turn (CPD-10); bound in `shadow` and `on`. */
export const AGENT_HARNESS_RESOLVED_ROUTING = 'AgentHarnessResolvedRouting';

/** The route a run that names no model pins (`grantedModel`): it resolves to the configured default. */
const ORG_DEFAULT_ROUTE = 'pipeshub:org-default';

/**
 * What a pin and the live layer read of an org's policy, by scope: the
 * deployment's own layers (its approval rules, its rules per agent, its flow
 * rule), then the org's own layers from the sources wired here
 * (`orgLayerSources`). Undefined while the approvals document is not wired.
 */
export function orgLayers(
  container: Container,
  dependencies: AgentHarnessDependencies,
  flow: FlowRule | null = deploymentFlow(flowSettings()).rule,
): OrgLayers | undefined {
  const approvals = dependencies.approvalSettings?.document;
  if (approvals === undefined) return undefined;
  const sources = orgLayerSources(container, dependencies);
  return async (orgId) => {
    const [document, own] = await Promise.all([
      approvals.read(),
      Promise.all(sources.map((source) => source(orgId))),
    ]);
    return new Map([
      ...deploymentLayers(document.value, flow),
      ...mergeOrgLayers(own),
    ]);
  };
}

/**
 * The team directory (CPD-11): the query service's, cached, bound once for
 * the pin, the Authorizer and the admin API. A host that hands its own
 * directory (`dependencies.directory`) keeps it (`bindDirectoryCaches`).
 */
export function directoryMembership(
  container: interfaces.Container,
  dependencies: AgentHarnessDependencies,
): MembershipPort | null {
  const bound = boundOrNull<MembershipPort>(
    container,
    CONTROL_PLANE_MEMBERSHIP,
  );
  if (bound !== null) return bound;
  if (dependencies.directory !== undefined || !container.isBound(APP_CONFIG))
    return null;
  const membership = new CachingMembershipPort(
    new HttpMembershipPort(() =>
      container.get<LegacyCapabilitySourceConfig>(APP_CONFIG),
    ),
    { now: () => Date.now() },
  );
  container
    .bind<MembershipPort>(CONTROL_PLANE_MEMBERSHIP)
    .toConstantValue(membership);
  return membership;
}

/** The app's configuration as the host binds it: where the query service is and the service secrets. */
const APP_CONFIG = 'AppConfig';

/**
 * `resolved_config` is required only while a live worker advertises it (the
 * availability is bound after this binder), and always once the policy store
 * is the record: the policy backing is bound before every other binder.
 */
export const pinWorkerFeatures = (
  container: interfaces.Container,
): PinWorkerFeatures => {
  const bound = (): WorkerFeatureAvailability | null =>
    boundOrNull<WorkerFeatureAvailability>(
      container,
      AGENT_HARNESS_WORKER_FEATURES,
    );
  return {
    pinOnly: policyStoreIsRecord(container),
    live: (feature) => bound()?.live(feature) ?? Promise.resolve(true),
    metrics: {
      withheld: (feature) => {
        bound()?.metrics.withheld(feature);
      },
    },
  };
};

/**
 * `CONTROL_PLANE_PIN` (05-migration-plan §1 rule 6, CPD-5): in `shadow` and
 * `on` admission's last policy step pins the run through the control plane.
 * Unset is `on` (C4.04); nothing is bound in `off`; `shadow` warns at startup
 * that the pin does not decide; an unknown value refuses to start.
 */
export const bindControlPlanePin: HarnessBinder = ({
  container,
  dependencies,
  logger,
}) => {
  if (container.isBound(AGENT_HARNESS_POLICY_PINNING)) return;
  const mode = parseControlPlanePin(
    {
      CONTROL_PLANE_PIN: process.env.CONTROL_PLANE_PIN,
    },
    envFlag('AGENT_HARNESS_WORKER_ENABLED'),
  );
  if (mode === 'off') return;
  if (mode === 'shadow') {
    logger?.warn(
      'cp.pin_shadow',
      harnessLogMeta('cp.pin_shadow', {
        flag: 'CONTROL_PLANE_PIN',
        gates: [...CONTROL_PLANE_PIN_GATES],
      }),
    );
  }
  const heads = createHeadRevisions();
  const labs = dependencies.rollout.settings?.bind(dependencies.rollout);
  const flow = deploymentFlow(flowSettings());
  for (const setting of flow.invalid) {
    logger?.warn(
      'approvals.flow_setting_invalid',
      harnessLogMeta('approvals.flow_setting_invalid', {
        reasonCode: 'flow_setting_invalid',
        setting,
      }),
    );
  }
  const layers = orgLayers(container, dependencies, flow.rule);
  const approvals = dependencies.approvalSettings?.document;
  const membership = directoryMembership(container, dependencies);
  const snapshots = new ResolvedSnapshotCache({
    load: policySnapshotLoader({
      heads,
      capabilities: {
        installations: createInstallationStore(),
        grants: createGrantStore(),
        bindings:
          dependencies.credentialBindings?.bindings ?? createBindingStore(),
        ledger: createCapabilityLedger(),
      },
      ...(layers !== undefined ? { layers } : {}),
      ...(membership !== null ? { membership } : {}),
      ...(labs !== undefined ? { rollout: { read: labs } } : {}),
      ...(dependencies.pin !== undefined
        ? { stages: dependencies.pin.stages, models: dependencies.pin.models }
        : {}),
    }),
    heads,
    // Membership does not move the head: a team change reaches pins within the directory's TTL.
    ...(membership !== null ? { maxAgeMs: DIRECTORY_TTL_MS } : {}),
  });
  // A snapshot's `dependsOn` names scopes while an event names the object written, so no event can be
  // matched to the entries it leaves untouched: every entry behind the write reloads.
  controlPlaneSubscribers.subscribe((event) => snapshots.dropBehind(event));
  container
    .bind<ResolvedSnapshotCache>(AGENT_HARNESS_PIN_SNAPSHOTS)
    .toConstantValue(snapshots);
  const refresh = (orgId: string): void => {
    snapshots.refresh(orgId);
  };
  const pinner = new PolicyRunConfigPinner({
    snapshots,
    engine: new PolicyResolver(),
    effectiveSet: new CanonicalEffectiveSetResolver(),
    releases: releaseLayers(),
    prefix: prefixSources({ modelAliases: [ORG_DEFAULT_ROUTE] }),
    run: runSources(PARENT_DIMENSIONS),
    parent: PARENT_DIMENSIONS,
    prices: usdPerMTok,
    roleBound: roleCostBound,
    // G-41: read when a pin is made; group access is bound after this binder.
    modelGroups: async (orgId, actorId, modelKey) => {
      const access = groupAccessOf(container);
      return access === undefined
        ? true
        : access.admits(orgId, actorId, { kind: 'model', modelKey });
    },
  });
  container
    .bind<AdminPinServices>(CONTROL_PLANE_PIN_SERVICES)
    .toConstantValue({ pinner, snapshots });
  container
    .bind<ResolvedRoutingDecider>(AGENT_HARNESS_RESOLVED_ROUTING)
    .toConstantValue(
      new ResolvedRoutingDecider({
        pinner,
        facts: new RoutingFactsReader({
          infrastructureReady: () => isAgentHarnessInfrastructureReady(),
          sessions: dependencies.sessions,
          // Bound after this binder: a worker is taken to be live until the availability is.
          liveness: pinWorkerFeatures(container),
        }),
      }),
    );
  if (mode === 'on') {
    const orgSections = !policyStoreIsRecord(container);
    container
      .bind<LegacyTurnHeader>(AGENT_HARNESS_LEGACY_TURN_HEADER)
      .toConstantValue(
        new LegacyTurnHeader({
          resolver: new LegacyTurnResolver({
            pinner,
            ...(approvals !== undefined
              ? {
                  unreadableApprovals: async (orgId, agentKey) =>
                    holdsUnreadableLayer(
                      (await approvals.read()).value,
                      orgId,
                      agentKey,
                      orgSections
                        ? harnessEditionOf(container).orgApprovalRules
                        : null,
                    ),
                }
              : {}),
          }),
          harnessOn: (orgId) => orgHarnessOn(orgId),
          // Both bound after this binder.
          authority: () =>
            boundOrNull<ResolvedTurnAuthority>(
              container,
              AGENT_HARNESS_RESOLVED_TURNS,
            ),
          directory: () =>
            boundOrNull<CachingAgentDirectory>(
              container,
              CONTROL_PLANE_AGENT_DIRECTORY,
            ),
        }),
      );
  }
  container.bind<ChildPinning>(AGENT_HARNESS_CHILD_PINNING).toConstantValue(
    new ChildPinning({
      mode,
      pinner,
      metrics: prometheusPinShadowMetrics,
      logger: logger ?? silentHarnessLogger,
      workerFeatures: pinWorkerFeatures(container),
      refresh,
    }),
  );
  container.bind<PolicyPinning>(AGENT_HARNESS_POLICY_PINNING).toConstantValue(
    new PolicyPinning({
      mode,
      pinner,
      metrics: prometheusPinShadowMetrics,
      logger: logger ?? silentHarnessLogger,
      rolloutFolded: labs !== undefined,
      refresh,
      workerFeatures: pinWorkerFeatures(container),
      ...(approvals !== undefined
        ? {
            approvals: workerApprovals(
              approvals,
              flow.rule,
              harnessEditionOf(container).orgApprovalRules,
            ),
          }
        : {}),
    }),
  );
};

/**
 * Who decides a turn's route (CPD-10 §4), by `CONTROL_PLANE_PIN`: the
 * resolved run configuration in `on`, today's decider otherwise.
 */
export function decidingRouter(
  container: interfaces.Container,
  today: RoutingDecider,
): RoutingDecider {
  const resolved = boundOrNull<ResolvedRoutingDecider>(
    container,
    AGENT_HARNESS_RESOLVED_ROUTING,
  );
  return resolved !== null && pinMode() === 'on'
    ? new OrgScopedRoutingDecider(
        (orgId) => orgHarnessOn(orgId),
        resolved,
        today,
      )
    : today;
}

const pinMode = (): ReturnType<typeof parseControlPlanePin> =>
  parseControlPlanePin(
    {
      CONTROL_PLANE_PIN: process.env.CONTROL_PLANE_PIN,
    },
    envFlag('AGENT_HARNESS_WORKER_ENABLED'),
  );

/**
 * Who routes a turn (CPD-10 §4), by `CONTROL_PLANE_PIN`: `off`, today's
 * decider; `shadow`, today's decider answers and the resolved one is compared
 * after the answer; `on`, the resolved one.
 */
export function routingDecider(
  container: interfaces.Container,
  today: RoutingDecider,
  logger?: Parameters<HarnessBinder>[0]['logger'],
): RoutingDecider {
  const resolved = boundOrNull<ResolvedRoutingDecider>(
    container,
    AGENT_HARNESS_RESOLVED_ROUTING,
  );
  if (resolved === null || pinMode() === 'on') {
    return decidingRouter(container, today);
  }
  return new ShadowRoutingDecider({
    today,
    resolved,
    metrics: prometheusPinShadowMetrics,
    logger: logger ?? silentHarnessLogger,
  });
}

/** The pinning step for the admission service, when one is bound. */
export const pinningSteps = (
  container: interfaces.Container,
): Pick<RunAdmissionServiceOptions, 'policySteps' | 'repin'> => {
  const step = boundOrNull<PolicyPinning>(
    container,
    AGENT_HARNESS_POLICY_PINNING,
  );
  return step === null
    ? {}
    : { policySteps: [step], repin: (context) => step.repin(context) };
};
