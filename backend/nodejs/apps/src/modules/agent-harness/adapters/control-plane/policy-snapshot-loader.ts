import {
  DEPLOYMENT_FLOW_SCOPE,
  DEPLOYMENT_SCOPE,
  deploymentAgentScope,
  loadCapabilityFacts,
  NO_FAMILIES,
  touchKey,
  type CapabilityFacts,
  type CapabilityStores,
  type HeadRevisions,
  type MembershipPort,
  type PolicyLayer,
  type Snapshot,
  type SnapshotLoader,
} from '../../../control-plane';
import { policyLayerSchema } from '../../contracts/generated/core/policy/policy-layer';
import type { ConfiguredModels } from '../../domain/configured-llms';
import {
  normalizeModelFamily,
  type HarnessRolloutSettings,
} from '../../domain/rollout';
import type { HarnessRolloutSettingsSource } from '../../ports/harness-rollout';
import type {
  ConfiguredLlmSource,
  LabsStagesSource,
} from '../../ports/pin-sources';

/**
 * An org's layers by scope (`deployment`, `deployment:flow`,
 * `deployment:agent:a7`, `org:o1`, `agent:a7`, `user:u3`), as the policy
 * store's mode serves them (`orgPolicyLayers`).
 */
export type OrgLayers = (
  orgId: string,
) => Promise<ReadonlyMap<string, PolicyLayer>>;

export interface PolicySnapshotLoaderOptions {
  heads: HeadRevisions;
  /** Absent: no stored policy (the store and the five documents are not wired). */
  layers?: OrgLayers;
  /** The org's Labs switches and its model families, folded as its `rollout` section (02-lld §11.1, CPD-10 §2). */
  rollout?: HarnessRolloutSettingsSource;
  /** The other Labs stages of the dimension table, in the same section. */
  stages?: LabsStagesSource;
  /** The deployment's configured models: the pin's `llm`, the models ceiling and the family rule. Absent: none configured. */
  models?: ConfiguredLlmSource;
  /** CPD-7's installations and grants, and whether the org is recorded as migrated; absent: the snapshot holds none, so the effective set is empty and is no authority. */
  capabilities?: CapabilityStores;
  /**
   * The actor's teams (CPD-11): their team grants and team-visible
   * installations. Unreadable membership fails the load (never "no teams").
   * Absent: the snapshot holds none.
   */
  membership?: MembershipPort;
}

const NO_MODELS: ConfiguredModels = { llms: [], families: NO_FAMILIES };

/** A family as a layer names it; one the contract's name pattern refuses could match no configured provider's row. */
const FAMILY_NAME = /^[a-z0-9][a-z0-9_.-]{0,63}$/;

/**
 * The org's `rollout` section with its Labs switches and stages over what it
 * stores, and its model families beside the routing lists it stores (null
 * narrows nothing, so no row). `CONTROL_PLANE_CAPABILITIES` is the migration
 * ledger's fact alone: a stored value is never read, and an org that is not
 * migrated has no row, so its pins keep the bytes they had.
 */
function foldedRollout(
  stored: NonNullable<PolicyLayer['rollout']>,
  stages: Partial<NonNullable<PolicyLayer['rollout']>>,
  labs: HarnessRolloutSettings | undefined,
  migrated: boolean,
): unknown {
  const { CONTROL_PLANE_CAPABILITIES: _stored, ...kept } = stored;
  const listed = labs?.modelFamilies ?? null;
  const families =
    listed === null
      ? undefined
      : [
          ...new Set(
            listed
              .map(normalizeModelFamily)
              .filter((family) => FAMILY_NAME.test(family)),
          ),
        ];
  return {
    ...kept,
    ...stages,
    ...(migrated ? { CONTROL_PLANE_CAPABILITIES: true } : {}),
    ...(labs !== undefined
      ? {
          ENABLE_AGENT_HARNESS: labs.orgEnabled,
          AGENT_HARNESS_KILL_SWITCH: labs.killSwitch,
        }
      : {}),
    ...(families !== undefined
      ? { durable: { ...kept.durable, modelFamilies: families } }
      : {}),
  };
}

const NO_CAPABILITIES: CapabilityFacts = {
  installations: new Map(),
  grants: new Map(),
  toolEffects: new Map(),
  bindings: [],
  migrated: false,
};

/**
 * What one pin reads, loaded once per (org, agent, actor) (02-lld §8.1): the
 * org head and the deployment's revision first, so the data is never older
 * than the revisions it is cached under, then the deployment's, org's,
 * agent's and actor's layers, the Labs stages and the configured models. A
 * Labs or policy save is a control-plane write, so it moves the org head and
 * the cache reloads; an AI-model save also moves the deployment's revision,
 * because every org reads that document. An installation or grant write
 * (CPD-7), a binding write (CPD-8) and the capability migration recording or
 * unrecording the org (CPD-10 R3) are control-plane writes too. The
 * actor's teams come from the directory (CPD-11) when one is wired; they do
 * not move the head, so the cache bounds an entry's age instead.
 */
export function policySnapshotLoader(
  options: PolicySnapshotLoaderOptions,
): SnapshotLoader {
  return async (orgId, agentKey, actorId, read): Promise<Snapshot> => {
    const heads = read ?? (await options.heads.heads(orgId));
    const actorTeams =
      options.membership === undefined
        ? []
        : (await options.membership.teamsOf(orgId, actorId)).map(
            (membership) => membership.teamId,
          );
    const [stored, labs, stages, models, { migrated, ...capabilities }] =
      await Promise.all([
        options.layers?.(orgId) ??
          Promise.resolve(new Map<string, PolicyLayer>()),
        options.rollout?.read(orgId),
        options.stages?.read(orgId),
        options.models?.read(orgId) ?? Promise.resolve(NO_MODELS),
        options.capabilities === undefined
          ? NO_CAPABILITIES
          : loadCapabilityFacts(options.capabilities, {
              orgId,
              agentKey,
              actorId,
              actorTeams,
            }),
      ]);
    const scopes = [
      DEPLOYMENT_SCOPE,
      DEPLOYMENT_FLOW_SCOPE,
      ...(agentKey !== null ? [deploymentAgentScope(agentKey)] : []),
      `org:${orgId}`,
      ...(agentKey !== null ? [`agent:${agentKey}`] : []),
      `user:${actorId}`,
    ];
    const layers = new Map<string, PolicyLayer>();
    for (const scope of scopes) {
      const layer = stored.get(scope);
      if (layer !== undefined) layers.set(scope, layer);
    }
    const org = layers.get(`org:${orgId}`);
    if (
      labs !== undefined ||
      stages !== undefined ||
      migrated ||
      org?.rollout?.CONTROL_PLANE_CAPABILITIES !== undefined
    ) {
      layers.set(
        `org:${orgId}`,
        policyLayerSchema.parse({
          ...(org ?? { schemaVersion: 1, scope: { kind: 'org', id: orgId } }),
          rollout: foldedRollout(
            org?.rollout ?? {},
            stages ?? {},
            labs,
            migrated,
          ),
        }),
      );
    }
    return {
      orgId,
      headRevision: heads.org,
      deploymentRevision: heads.deployment,
      models: models.llms,
      families: models.families,
      agentKey,
      actorId,
      ...capabilities,
      layers,
      actorTeams,
      agentOwnerTeam: null,
      // An AI-model save names this object in its change event: the models are every snapshot's.
      dependsOn: new Set([...scopes, touchKey('ai-models', 'aiModels')]),
    };
  };
}
