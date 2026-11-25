import type { EffectiveRunConfig } from '../contracts/generated/core/policy/effective-run-config';
import {
  policyLayerSchema,
  type PolicyLayer,
} from '../contracts/generated/core/policy/policy-layer';
import { llmRoute, type ResolvedRunConfig } from '../../control-plane';
import type { AgentRunDefinition } from './agent-definition';
import { definitionRoute, roleRoutesOf } from './child-model';
import type { EffectiveApprovals } from './approvals';
import { approvalCeiling } from './policy/approval-rules';
import { pinnedEngineProfileSchema } from '../contracts/generated/core/engine-profile';
import { RELEASE_DEFAULTS } from '../contracts/generated/core/policy-data';
import {
  RELEASE_ENGINE_PROFILES,
  type EngineProfileNarrowing,
  type PinnedEngineProfile,
} from './engine-profiles';
import type { ExecutorKind, RunBudgets } from './run-spec';
import type { RunTrigger } from './schedules';

/**
 * The `dimension` label of `pipeshub_cp_shadow_mismatch_total` for what CPD-5
 * compares (05-migration-plan §1 rule 6): each names a decision today's
 * admission takes that the pin also holds. `refused` is a pin the control
 * plane refused although today admitted the run; `unavailable` a pin it could
 * not make.
 */
export type PinShadowDimension =
  | 'engine_profile'
  | 'budgets'
  | 'models'
  | 'approvals'
  | 'rollout'
  | 'routing'
  | 'refused'
  | 'unavailable';

/** What today's admission decided, in the terms the pin is compared in. */
export interface TodayPolicy {
  executor: ExecutorKind;
  engineProfile: PinnedEngineProfile | null;
  budgets: RunBudgets;
  /** `provider:model` of the route the claim pins (`grantedModel`). */
  modelRoute: string;
  /** The configured model the run names, when it names one. */
  modelKey?: string;
  /** What the worker would decide per call, when the admission can read it (shadow only). */
  approvals?: EffectiveApprovals;
}

/** A layer of `kind`, held to the policy-layer contract (a definition's narrowing arrives unparsed). */
const layerOf = (
  kind: PolicyLayer['scope']['kind'],
  sections: Record<string, unknown>,
): PolicyLayer =>
  policyLayerSchema.parse({ schemaVersion: 1, scope: { kind }, ...sections });

/**
 * The agent's definition as an `agent` layer: its engine narrowing, the
 * models it grants (the first is its default) and the model of each sub-agent
 * role (MA1). A role's model lies within the routes the layer grants; a
 * definition that lists no models grants none, and the org's ceiling stays.
 * The graph holds definitions until CPD-7/CPD-9; the admission reads it and
 * the pin folds it.
 */
export function definitionLayer(
  definition: AgentRunDefinition | null,
): PolicyLayer | undefined {
  if (definition === null) return undefined;
  const routes = definition.models.map((model) =>
    definitionRoute(model.provider, model.modelName),
  );
  const roles = roleRoutesOf(definition);
  const named = Object.keys(roles).length > 0;
  const granted = [...new Set([...routes, ...Object.values(roles)])].sort();
  return layerOf('agent', {
    ...(definition.engineProfile !== undefined &&
    definition.engineProfile !== null
      ? { engineProfile: definition.engineProfile }
      : {}),
    ...(routes.length > 0 || named
      ? {
          models: {
            ...(routes.length > 0
              ? { routes: granted, primary: routes[0] }
              : {}),
            ...(named ? { roles } : {}),
          },
        }
      : {}),
  });
}

/** A firing's cost cap as a `trigger` layer (P8a). */
export const triggerLayer = (
  trigger: Pick<RunTrigger, 'maxCostUsd'> | undefined,
): PolicyLayer | undefined =>
  trigger?.maxCostUsd === undefined
    ? undefined
    : layerOf('trigger', { budgets: { maxCostUsd: trigger.maxCostUsd } });

/**
 * What the request asks: its deadline as the active-time budget, its reasoning
 * effort as an engine narrowing, and the model it chose.
 */
export function requestLayer(
  deadlineMs: number,
  narrowing: EngineProfileNarrowing | undefined,
  modelRoute: string,
): PolicyLayer {
  return layerOf('request', {
    budgets: { activeTimeS: Math.max(1, Math.ceil(deadlineMs / 1000)) },
    ...(narrowing !== undefined ? { engineProfile: narrowing } : {}),
    models: { primary: modelRoute },
  });
}

/**
 * SPENDCONT: the token backstop a run's kind of work gets. Engine work (an engine run, or a loop run that may hand
 * off to one, whose tree pays for the engine child) keeps `maxTokens`; a loop run that cannot hand off is held to
 * `loopMaxTokens`. One Codex child read 3.47M prompt tokens, mostly cached, for USD 0.13 and was stopped by the old
 * 1M token cap with USD 4.87 of 5 left.
 */
export const workTokens = (maxTokens: number, engineWork: boolean): number =>
  engineWork
    ? maxTokens
    : Math.min(maxTokens, RELEASE_DEFAULTS.budgets.loopMaxTokens);

const value = (config: EffectiveRunConfig, path: string): unknown =>
  config.dimensions[path]?.value;

const atMost = (budget: unknown, limit: unknown): number => {
  const bound = typeof limit === 'number' ? limit : Number.POSITIVE_INFINITY;
  return Math.min(budget as number, bound);
};

/**
 * Budgets and engine limits are one dimension (CPD-5): the run's budgets are
 * `budgets.*`, each met with the engine profile's limit when the run is an
 * engine run. `activeTimeS` unset means the request's deadline did not reach
 * the pin, which cannot happen for an admitted run.
 */
export function pinnedBudgets(
  config: EffectiveRunConfig,
  executor: ExecutorKind,
  handsOff = false,
): RunBudgets {
  const limit = (path: string): unknown =>
    executor === 'engine'
      ? value(config, `engineProfile.limits.${path}`)
      : null;
  return {
    maxTurns: value(config, 'budgets.maxTurns') as number,
    maxTokens: atMost(
      workTokens(
        value(config, 'budgets.maxTokens') as number,
        executor === 'engine' || handsOff,
      ),
      limit('maxTokens'),
    ),
    maxCostUsd: atMost(value(config, 'budgets.maxCostUsd'), limit('maxUsd')),
    activeTimeS: atMost(
      value(config, 'budgets.activeTimeS'),
      limit('maxWallS'),
    ),
  };
}

const PROFILE_FIELDS = [
  'engine',
  'mode',
  'isolationMin',
  'egress',
  'tools',
  'limits',
  'subAgents',
  'reasoningEffort',
  'approvals',
  'skills',
  'accountKinds',
] as const;

/** The engine profile as the pin holds it: the release candidate's engine and every field folded under it. */
export function pinnedProfileFields(
  config: EffectiveRunConfig,
): Record<string, unknown> {
  const fields: Record<string, unknown> = {};
  for (const [path, entry] of Object.entries(config.dimensions)) {
    if (!path.startsWith('engineProfile.')) continue;
    const keys = path.slice('engineProfile.'.length).split('.');
    let node = fields;
    for (const key of keys.slice(0, -1)) {
      node[key] ??= {};
      node = node[key] as Record<string, unknown>;
    }
    node[keys[keys.length - 1] as string] = entry.value;
  }
  return fields;
}

const canonical = (value: unknown): string =>
  JSON.stringify(value, (_key, inner: unknown) =>
    inner !== null && typeof inner === 'object' && !Array.isArray(inner)
      ? Object.fromEntries(
          Object.entries(inner as Record<string, unknown>).sort(([a], [b]) =>
            a < b ? -1 : a > b ? 1 : 0,
          ),
        )
      : inner,
  );

type PinnedLlm = NonNullable<ResolvedRunConfig['llm']>;

/**
 * The pin calls the model today's claim would: the same route, or the
 * configured entry of the key the run names (a client may leave the provider
 * or the name out of its route; the pin names the model as it is configured).
 */
const sameModel = (
  today: TodayPolicy,
  primary: unknown,
  llm: PinnedLlm | null,
): boolean =>
  primary === today.modelRoute ||
  (llm !== null &&
    today.modelKey !== undefined &&
    llm.modelKey === today.modelKey &&
    primary === llmRoute(llm.provider, llm.model));

/** What a comparison knows beside the two decisions. */
export interface PinComparison {
  /** The pin's configured model, which names the model a client's route may leave half-said. */
  llm?: PinnedLlm | null;
  /** False where the host's rollout policy exposes no switches to fold: the pin then holds none to compare. */
  rolloutFolded?: boolean;
}

/** The dimensions on which the pin and today's decision disagree, sorted; empty when they agree. */
export function pinShadowMismatches(
  today: TodayPolicy,
  pin: EffectiveRunConfig,
  compared: PinComparison = {},
): PinShadowDimension[] {
  const { llm = null, rolloutFolded = true } = compared;
  const found = new Set<PinShadowDimension>();
  if (today.engineProfile !== null) {
    const pinned = pinnedProfileFields(pin);
    const theirs = today.engineProfile as unknown as Record<string, unknown>;
    if (
      PROFILE_FIELDS.some(
        (field) => canonical(pinned[field]) !== canonical(theirs[field]),
      )
    ) {
      found.add('engine_profile');
    }
  }
  if (
    canonical(pinnedBudgets(pin, today.executor)) !== canonical(today.budgets)
  ) {
    found.add('budgets');
  }
  if (!sameModel(today, value(pin, 'models.primary'), llm)) found.add('models');
  if (
    today.approvals !== undefined &&
    canonical(
      value(pin, 'approvals') ?? approvalCeiling({ classes: {}, tools: {} }),
    ) !== canonical(today.approvals)
  ) {
    found.add('approvals');
  }
  if (
    rolloutFolded &&
    (value(pin, 'rollout.ENABLE_AGENT_HARNESS') !== true ||
      value(pin, 'rollout.AGENT_HARNESS_KILL_SWITCH') === true)
  ) {
    found.add('rollout');
  }
  return [...found].sort();
}

/** The model the claim pins for a loop run, as a route id (`grantedModel`). */
export const modelRouteOf = (route: {
  provider: string;
  model: string;
}): string => `${route.provider}:${route.model}`;

/**
 * The release layers a pin may start from, one per released engine in
 * release order: the pinner takes the first whose engine every layer admits,
 * as `resolveEngineProfile` does.
 */
export const releaseLayers = (): readonly unknown[] =>
  RELEASE_ENGINE_PROFILES.map((profile) => ({
    ...RELEASE_DEFAULTS,
    engineProfile: profile,
  }));

/**
 * The engine profile a pin holds, whole: what an engine run executes under,
 * and what a loop parent may grant an engine child (the release under the
 * org's ceiling and the agent's layer). Null when the pin does not hold one
 * the contract accepts.
 */
/** CTL-6: the engine authority a loop run pinned for its `code_task` child (`x.pipeshub.codeTask`); null: none. */
export function codeTaskAuthority(spec: {
  extensions?: Record<string, unknown> | null;
}): PinnedEngineProfile | null {
  const extension = spec.extensions?.['x.pipeshub'] as
    { codeTask?: { profile?: unknown } } | undefined;
  const parsed = pinnedEngineProfileSchema.safeParse(
    extension?.codeTask?.profile,
  );
  return parsed.success ? parsed.data : null;
}

export function pinnedEngineProfile(
  config: EffectiveRunConfig,
): PinnedEngineProfile | null {
  const parsed = pinnedEngineProfileSchema.safeParse(
    pinnedProfileFields(config),
  );
  return parsed.success ? parsed.data : null;
}
