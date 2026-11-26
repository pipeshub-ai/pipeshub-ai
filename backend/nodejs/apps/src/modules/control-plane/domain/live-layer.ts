import type { LiveLayer } from '../contracts/generated';
import type { PolicyLayer } from './policy-alias';
import type { WebsitePolicy } from './websites';
import type { RepositoryPolicy } from './repositories';

type ScopedLiveLayer = LiveLayer['layers'][number];

const DEPLOYMENT = 'deployment';
const DEPLOYMENT_AGENT = 'deployment:agent:';
const AGENT = 'agent:';

/**
 * What of a stored layer narrows running work: the ceilings a worker or a
 * gateway meets into a run's pin. Who approves, memory, retention, locks and
 * edit stamps are not authority a run holds, so they never leave Node here.
 */
function narrowing(layer: PolicyLayer): PolicyLayer | null {
  const live: PolicyLayer = { schemaVersion: 1, scope: { kind: 'live' } };
  if (layer.approvals !== undefined) live.approvals = layer.approvals;
  if (layer.engineProfile !== undefined) {
    live.engineProfile = layer.engineProfile;
  }
  if (layer.budgets !== undefined) live.budgets = layer.budgets;
  if (layer.models !== undefined) {
    const { routes, disabledRules, consumerPlans } = layer.models;
    const models = {
      ...(routes !== undefined ? { routes } : {}),
      ...(disabledRules !== undefined ? { disabledRules } : {}),
      ...(consumerPlans !== undefined ? { consumerPlans } : {}),
    };
    if (Object.keys(models).length > 0) live.models = models;
  }
  return Object.keys(live).length > 2 ? live : null;
}

/** The agent a stored scope's layer narrows; `null` for every run of the org, `undefined` when it narrows no run. */
function agentOf(scope: string, orgId: string): string | null | undefined {
  if (scope === `org:${orgId}`) return null;
  if (scope.startsWith(DEPLOYMENT_AGENT)) {
    return scope.slice(DEPLOYMENT_AGENT.length);
  }
  // Every other layer of the deployment (its default rules, its flow rule) narrows every run.
  if (scope === DEPLOYMENT || scope.startsWith(`${DEPLOYMENT}:`)) return null;
  if (scope.startsWith(AGENT)) return scope.slice(AGENT.length);
  return undefined;
}

export interface LiveLayerInput {
  readonly orgId: string;
  /** The org head the layers were read at. */
  readonly revision: number;
  /** The org's stored layers by scope: `deployment`, `deployment:flow`, `deployment:agent:<key>`, `org:<id>`, `agent:<key>`, `user:<id>`. */
  readonly layers: ReadonlyMap<string, PolicyLayer>;
  /** The org's kill switch (Labs). */
  readonly killSwitch: boolean;
  /** The deployment's cap on allowed websites (WP-63), from its settings rather than a stored layer; null: none. */
  readonly websitesCap?: WebsitePolicy | null;
  /** The deployment's cap on allowed git hosts (WP-63 part A, `HARNESS_ALLOWED_REPOSITORIES_CAP`); null: none. */
  readonly repositoriesCap?: RepositoryPolicy | null;
  /** Sections an edition adds whose org value travels beside the layers (`PolicySectionRule.live`); absent: none. */
  readonly carried?: readonly string[];
}

type LiveWebsites = NonNullable<LiveLayer['websites']>;

type LiveRepositories = NonNullable<LiveLayer['repositories']>;

/**
 * Allowed git hosts by who set them (WP-63 part A): the deployment's cap,
 * the org's list and each agent's narrowing, which travels only when its
 * layer sets one.
 */
function repositoriesOf(input: LiveLayerInput): LiveRepositories | null {
  const agents: Record<string, RepositoryPolicy> = {};
  for (const [scope, layer] of input.layers) {
    if (scope.startsWith(AGENT) && layer.repositories !== undefined) {
      agents[scope.slice(AGENT.length)] = layer.repositories;
    }
  }
  const deployment = input.repositoriesCap ?? null;
  const org = input.layers.get(`org:${input.orgId}`)?.repositories ?? null;
  const repositories: LiveRepositories = {
    ...(deployment !== null ? { deployment } : {}),
    ...(org !== null ? { org } : {}),
    ...(Object.keys(agents).length > 0 ? { agents } : {}),
  };
  return Object.keys(repositories).length > 0 ? repositories : null;
}

/**
 * Allowed websites by who set them (WP-63): kept apart, since an org that
 * lists none allows none while a deployment that lists none caps none. An
 * agent's section travels only when its layer sets one.
 */
function websitesOf(input: LiveLayerInput): LiveWebsites | null {
  const agents: Record<string, WebsitePolicy> = {};
  for (const [scope, layer] of input.layers) {
    if (scope.startsWith(AGENT) && layer.websites !== undefined) {
      agents[scope.slice(AGENT.length)] = layer.websites;
    }
  }
  const deployment =
    input.websitesCap ?? input.layers.get(DEPLOYMENT)?.websites ?? null;
  const org = input.layers.get(`org:${input.orgId}`)?.websites ?? null;
  const websites: LiveWebsites = {
    ...(deployment !== null ? { deployment } : {}),
    ...(org !== null ? { org } : {}),
    ...(Object.keys(agents).length > 0 ? { agents } : {}),
  };
  return Object.keys(websites).length > 0 ? websites : null;
}

/**
 * An org's live layer (02-lld §8.3, live-layer.schema.json): each stored
 * layer that narrows running work, as it is, re-scoped to `live`. Nothing is
 * folded here: the worker meets the layers, so two may name one agent (the
 * deployment's rules and the org's). A user's layer holds choices only and
 * is left out. Ordered by scope, so the same layers answer the same bytes.
 * The org layer's sections an edition carries (`carried`) travel beside them, as they are.
 */
export function liveLayerOf(input: LiveLayerInput): LiveLayer {
  const layers: ScopedLiveLayer[] = [
    {
      layer: {
        schemaVersion: 1,
        scope: { kind: 'live' },
        rollout: { AGENT_HARNESS_KILL_SWITCH: input.killSwitch },
      },
    },
  ];
  for (const scope of [...input.layers.keys()].sort()) {
    const agentKey = agentOf(scope, input.orgId);
    if (agentKey === undefined) continue;
    const layer = narrowing(input.layers.get(scope) as PolicyLayer);
    if (layer === null) continue;
    layers.push(agentKey === null ? { layer } : { agentKey, layer });
  }
  // Not dimensions a layer narrows: the org's own documents, which the workers and gateways apply as they are.
  const org = input.layers.get(`org:${input.orgId}`) as
    Record<string, unknown> | undefined;
  const carried = Object.fromEntries(
    (input.carried ?? []).flatMap((section) => {
      const value = org?.[section];
      return value !== undefined ? [[section, value] as const] : [];
    }),
  );
  const websites = websitesOf(input);
  const repositories = repositoriesOf(input);
  return {
    schemaVersion: 1,
    orgId: input.orgId,
    revision: input.revision,
    layers,
    ...carried,
    ...(websites !== null ? { websites } : {}),
    ...(repositories !== null ? { repositories } : {}),
    revokedBindings: [],
  };
}
