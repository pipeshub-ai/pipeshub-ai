import { resolveDeliverable } from './deliverable';
import type { RunSurface } from './admission';
import type { RunAudience } from './session-audience';
import type { RunTrigger } from './schedules';
import type { MemoryPin } from './memory';
import type { ResolvedRunConfig } from '../../control-plane';
import {
  canonicalDigest,
  canonicalJsonBytes,
} from '../contracts/canonical-json';
import {
  PIPESHUB_CONTRACT,
  RELEASE_DEFAULTS,
} from '../contracts/generated/core/policy-data';
import { RunSpec, runSpecSchema } from '../contracts/generated/core/run-spec';
import {
  PipeshubRunSpecExtension,
  pipeshubRunSpecExtensionSchema,
} from '../contracts/generated/ext/pipeshub/run-spec';
import { AgentRunDefinition } from './agent-definition';
import {
  engineExecutor,
  engineRef,
  PinnedEngineProfile,
} from './engine-profiles';
import type { PinnedModelAccess } from './model-access';
import {
  CapabilitySnapshot,
  DeploymentCapabilities,
  provesCodeSandbox,
} from './capabilities';
import { HarnessConflictError, HarnessForbiddenError } from './errors';
import { HarnessRun, RunInputSnapshot } from './execution';
import { admittedRunKind } from './run-kind';
import {
  assistantAgentLoopMode,
  ASSISTANT_AGENT_DEFINITION_KEY,
  isDurableChatMode,
  modeMaxChildren,
} from './rollout';
import { legacySkillPinDigest } from './skill-bundle';
import { pinnedBudgets, workTokens } from './pin-shadow';
import {
  GITHUB_PULL_REQUEST_TOOL,
  GITHUB_TOOLSET,
  type SessionRepository,
} from './repository';
import type { RunGates } from './run-gates';
import { ENVIRONMENT_PIN_FEATURE, pinsEnvironment } from './agent-environment';
import { type AnsweredRunPin, ownCostCap } from './answered-run';

export const RUN_SPEC_SCHEMA_VERSION = 3;
export type RunExecutor = NonNullable<RunSpec['executor']>;
export type ExecutorKind = RunExecutor['kind'];

/** A run without a resolved engine profile runs on the loop. */
export const LOOP_EXECUTOR: Readonly<RunExecutor> = Object.freeze({
  kind: 'loop',
});
// Must equal harness_core FOLD_VERSION (checked through contracts/harness/vectors/pinned-run-spec.json).
export const RUN_SPEC_FOLD_VERSION = 8;
/** Until the prompt registry (P4) versions prompts, every run pins this one. */
export const RUN_SPEC_PROMPT_VERSION = 'pipeshub-agent-2026-09';
/**
 * Per-run limits: the release's budgets (`release.json`, CPD-5). `maxTurns`
 * is the legacy agent loop's own limit, so a durable run gets the same turn
 * budget as the route it replaces. `maxChildren` is 0 unless the agent
 * definition grants delegation (P6.1). The USD cap is the real limit; the
 * token caps are runaway backstops: `maxTokens` for coding-engine work,
 * `loopMaxTokens` for a loop run that cannot hand off to an engine.
 */
export const RUN_SPEC_BUDGETS = Object.freeze({
  maxTurns: RELEASE_DEFAULTS.budgets.maxTurns,
  maxTokens: RELEASE_DEFAULTS.budgets.maxTokens,
  loopMaxTokens: RELEASE_DEFAULTS.budgets.loopMaxTokens,
  maxCostUsd: RELEASE_DEFAULTS.budgets.maxCostUsd,
  maxChildren: 0,
});
/** A pinned spec lives on the run document as well as in the journal. */
export const MAX_PINNED_SPEC_BYTES = 4 * 1024 * 1024;

export type RunVia = PipeshubRunSpecExtension['via'];

export interface PinnedRunSpec {
  spec: RunSpec;
  /** Digest of the spec as pinned; the identity of the pin, never recomputed. */
  specDigest: `sha256:${string}`;
  capabilities: DeploymentCapabilities;
  capabilitiesDigest: `sha256:${string}`;
}

/** What a claim resolves a spec from: all of it fixed at admission. */
export interface SpecSubject {
  run: HarnessRun;
  /** Null for built-in chat. */
  agentDefinition: AgentRunDefinition | null;
  via: RunVia;
  /** Where the run was started; null or absent: not recorded (a child, or a run admitted before FU4). */
  surface?: RunSurface | null;
  /** The profile admission resolved (E4a); null or absent: the run is not an engine run. */
  engineProfile?: PinnedEngineProfile | null;
  /** The model access admission resolved (A1); null or absent: the route is the input's, as before A1. */
  modelAccess?: PinnedModelAccess | null;
  /** Whose turns the run may see (SS2); null or absent: a personal session, all of it. */
  audience?: RunAudience | null;
  /** The schedule firing the run is (P8a); null or absent: a person's turn. */
  trigger?: RunTrigger | null;
  /** Governed memory (P7.3); null or absent: none. */
  memory?: MemoryPin | null;
  /** The run's pin (CPD-5 `on`); null or absent: today's resolution. */
  resolved?: ResolvedRunConfig | null;
  /** DEEPROUTE: false when admission found the org's automatic hand-off off; null or absent: on. */
  codeTaskAutoRoute?: boolean | null;
  /** GIT-FLOW (WP-26): the chat's repository admission kept on the run; null or absent: none. */
  repository?: SessionRepository | null;
  /** G-03..G-05: the run gates the turn asked for, pinned as `RunSpec.gates`; null or absent: none. */
  gates?: RunGates | null;
  /** WP-64: the run this one answers and the spending cap it carries on; null or absent: not an answer. */
  answered?: AnsweredRunPin | null;
  /**
   * C2.20: each engine's run image digest as the sandbox broker reported it at
   * this claim (`kind@version` -> `sha256:…`); null or absent: not known, and an
   * engine run pins no image (its worker uses the image the broker has).
   */
  engineImages?: ReadonlyMap<string, string> | null;
}

export type RunSpecResolver = (
  subject: SpecSubject,
  capabilities: CapabilitySnapshot,
) => PinnedRunSpec;

/** What decides how a run executes: the agent's grant, the admitted input and the resolved profile. */
export interface ExecutorSubject {
  agentDefinition: AgentRunDefinition | null;
  input: RunInputSnapshot;
  /** Resolved once at admission and kept on the run, so the claim pins the same one (E4a). */
  engineProfile: PinnedEngineProfile | null;
  /** CTL-6: the profile is the loop's engine authority for `code_task`, not the run's executor. */
  codeTask?: boolean;
}

/**
 * How a run executes. Admission asks it before it admits (who may start
 * engine work, SS1) and the claim pins its answer, so the two never differ.
 */
export type ExecutorResolver = (subject: ExecutorSubject) => RunExecutor;

/**
 * The one resolver (E4a, replacing SS1's `loopExecutor`): an engine run when
 * admission resolved a profile, pinned with that profile and its digest; the
 * loop otherwise.
 */
export const profileExecutor: ExecutorResolver = ({
  engineProfile,
  codeTask,
}) =>
  engineProfile === null || codeTask === true
    ? LOOP_EXECUTOR
    : engineExecutor(engineProfile);

const NAME = /^[a-z0-9][a-z0-9_.-]{0,63}$/;
/** An opaque identifier as `common.schema.json` Id gives it. */
export const OPAQUE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const ID = OPAQUE_ID;
const TOOL_SOURCE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const TOOL_INSTANCE = /^[A-Za-z0-9_-]{1,128}$/;
const TOOL_NAME = /^[A-Za-z0-9_.-]{1,128}$/;
const EFFORT_RANK: Readonly<Record<string, number>> = {
  minimal: 0,
  low: 1,
  medium: 2,
  high: 3,
};
const WEB_SEARCH_TOOL = 'web_search';

/** Admission sets the principal (I3); the spec names the same one. */
export const principalRefFor = (actorId: string): string => `p_${actorId}`;

const unresolvable = (message: string): HarnessConflictError =>
  new HarnessConflictError('SPEC_UNRESOLVABLE', message);

/** How a provider or mode is written into a pinned spec (Python `machine_name`). */
export const machineName = (
  value: string | undefined,
  fallback: string,
): string => {
  const name = (value ?? '').trim().toLowerCase();
  return NAME.test(name) ? name : fallback;
};

const byKey =
  <T>(key: (item: T) => string) =>
  (a: T, b: T): number =>
    key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0;

type ModelChoice = {
  route: RunSpec['model']['policy'][number];
  model?: NonNullable<PipeshubRunSpecExtension['model']>;
  /** G-48: the routes the run may move to when its own model's provider is down (P4.7 model policy). */
  fallbacks?: RunSpec['model']['policy'];
};

/** At most this many alternates are pinned: a provider outage moves a run at most this many times. */
export const MAX_ALTERNATE_MODELS = 2;
/**
 * The failures an alternate model takes over (G-48): an outage, never a content filter. Moving to another model
 * to get past a filter would weaken it; budget and credential refusals are never triggers (P4.7).
 */
export const ALTERNATE_MODEL_ON = ['5xx', 'timeout', '429_exhausted'] as const;

/**
 * G-48: the alternates of a loop run's model, in the agent's own order: the other models the agent grants (its
 * owner allowed each). An agent with one model, or none (the org's default), has no alternate, so a provider
 * outage ends as before (`provider_unavailable`). The worker reaches each through the org's configured models and
 * refuses one it no longer configures (`ConfiguredRoutePorts`).
 */
export function alternateModelRoutes(
  definition: AgentRunDefinition | null,
  chosen: RunSpec['model']['policy'][number],
): RunSpec['model']['policy'] {
  const seen = new Set([`${chosen.provider}:${chosen.model}`]);
  const routes: RunSpec['model']['policy'] = [];
  for (const model of definition?.models ?? []) {
    const route = {
      provider: machineName(model.provider, 'unknown'),
      model: model.modelName,
    };
    const key = `${route.provider}:${route.model}`;
    if (seen.has(key)) continue;
    seen.add(key);
    routes.push({ ...route, on: [...ALTERNATE_MODEL_ON] });
    if (routes.length === MAX_ALTERNATE_MODELS) break;
  }
  return routes;
}

/** The route a run that names no model pins: an alias the worker resolves to the configured default. */
export const ORG_DEFAULT_MODEL = {
  provider: 'pipeshub',
  model: 'org-default',
} as const;

/**
 * The model the run uses. An agent that lists models grants only those; the
 * client may choose among them, never add one. An agent without models (or
 * built-in chat) runs on the org's configured models.
 */
export function grantedModel(
  definition: AgentRunDefinition | null,
  requested: RunInputSnapshot['model'],
): ModelChoice {
  const granted = definition?.models ?? [];
  if (granted.length > 0) {
    const chosen =
      requested.modelKey === undefined
        ? granted[0]
        : granted.find(
            (model) =>
              model.modelKey === requested.modelKey &&
              (requested.modelName === undefined ||
                model.modelName === requested.modelName),
          );
    if (chosen === undefined) {
      throw new HarnessForbiddenError('The agent does not grant this model');
    }
    return {
      route: {
        provider: machineName(chosen.provider, 'unknown'),
        model: chosen.modelName,
      },
      model: { modelKey: chosen.modelKey, modelName: chosen.modelName },
    };
  }
  if (requested.modelKey === undefined) {
    return { route: { ...ORG_DEFAULT_MODEL } };
  }
  const modelName = requested.modelName ?? requested.modelKey;
  return {
    route: {
      provider: machineName(requested.modelProvider, 'unknown'),
      model: modelName,
    },
    model: { modelKey: requested.modelKey, modelName },
  };
}

/**
 * The model route a claim pins: the access admission resolved for an engine
 * run (A1: the concrete model and its access route), else the input's choice.
 */
function pinnedModel(
  definition: AgentRunDefinition | null,
  input: RunInputSnapshot,
  access: PinnedModelAccess | null,
  /** G-48: a live worker moves a run whose provider is down (`model_fallback`): pin the agent's other models. */
  alternates = false,
): ModelChoice {
  if (access === null) {
    const granted = grantedModel(definition, input.model);
    if (!alternates) return granted;
    const fallbacks = alternateModelRoutes(definition, granted.route);
    return fallbacks.length > 0 ? { ...granted, fallbacks } : granted;
  }
  const fallbacks = alternates
    ? engineAlternates(
        access.access,
        alternateModelRoutes(definition, access.route),
      )
    : [];
  return {
    route: { ...access.route, access: { ...access.access } },
    ...(access.model !== undefined ? { model: { ...access.model } } : {}),
    ...(fallbacks.length > 0 ? { fallbacks } : {}),
  };
}

/**
 * G-48 (WP-100): an engine run's alternates, reached as its own model is when that is the org's configured key
 * through the Model Gateway, which moves the engine's calls to them by the kernel's policy. A person's own key, an
 * org cloud account or a vendor plan pays for its own model only (A1), so a run on one has none.
 */
export function engineAlternates(
  primary: PinnedModelAccess['access'],
  candidates: RunSpec['model']['policy'],
): RunSpec['model']['policy'] {
  if (
    primary.route !== 'gateway' ||
    primary.accountKind !== 'org_api_key' ||
    primary.accountId !== undefined ||
    primary.pays !== 'org'
  ) {
    return [];
  }
  return candidates.map((route) => ({
    provider: route.provider,
    model: route.model,
    ...(route.on !== undefined ? { on: [...route.on] } : {}),
    access: { route: 'gateway', accountKind: 'org_api_key', pays: 'org' },
  }));
}

/** Admission refuses what the claim could never resolve (P-14). */
export function assertClientNarrowing(
  definition: AgentRunDefinition | null,
  input: RunInputSnapshot,
): void {
  grantedModel(definition, input.model);
}

const toolId = (
  source: string,
  instance: string,
  tool: string,
): string | null =>
  TOOL_SOURCE.test(source) &&
  TOOL_INSTANCE.test(instance) &&
  TOOL_NAME.test(tool)
    ? `${source}@${instance}/${tool}`
    : null;

type ToolGrant = RunSpec['grants']['tools'][number];
type McpPin = PipeshubRunSpecExtension['mcp'][number];

/** The worker feature a run whose claim carries `x.pipeshub.resolved` requires (`worker-compat`). */
export const RESOLVED_CONFIG_FEATURE = 'resolved_config' as const;

/** FU4: a spec pinning where the run was started (`x.pipeshub.surface`); an older worker's strict model refuses it. */
export const RUN_SURFACE_FEATURE = 'run_surface' as const;

/**
 * C2.07: a spec pinning what the run was admitted under (`x.pipeshub.release`)
 * and an engine run's image digest (`pins.sandboxImage`); an older worker's
 * strict model refuses the first and would ignore the second.
 */
export const RELEASE_PIN_FEATURE = 'release_pin' as const;

/**
 * ADR-RUN-01 D1: a spec pinning what the run owes (`x.pipeshub.deliverable`), resolved once from its request; an
 * older worker's strict model refuses it, and a run without it resolves the deliverable itself when it loads.
 */
export const DELIVERABLE_PIN_FEATURE = 'deliverable_pin' as const;

/**
 * G-48: the deployment moves a run whose model provider is down to an alternate route the run pinned (P4.7; a loop
 * run in the worker's kernel, an engine's calls in the Model Gateway, WP-100; `HARNESS_MODEL_FALLBACK`, on by
 * default). A deployment fact, like `vendor_signin`: Node pins an agent's other models as alternates only while a
 * live worker advertises it, and such a run requires it. No run admitted before it pinned one.
 */
export const MODEL_FALLBACK_FEATURE = 'model_fallback' as const;

/** The `/pipeshub` contract version this release's engine images declare (CONTRACT-V1). */
export const PIPESHUB_CONTRACT_VERSION: number = PIPESHUB_CONTRACT.version;

type ReleasePin = NonNullable<PipeshubRunSpecExtension['release']>;

/**
 * One digest over what the run may call: its tool grants (each with its
 * descriptor digest), its MCP manifests and its skill bundles, as pinned.
 */
export const catalogDigest = (
  tools: RunSpec['grants']['tools'],
  extension: Pick<PipeshubRunSpecExtension, 'mcp' | 'skills'>,
): `sha256:${string}` =>
  canonicalDigest({ tools, mcp: extension.mcp, skills: extension.skills });

function releasePin(
  tools: RunSpec['grants']['tools'],
  extension: PipeshubRunSpecExtension,
  resolved: ResolvedRunConfig | null,
): ReleasePin {
  return {
    pipeshubContract: PIPESHUB_CONTRACT_VERSION,
    catalogDigest: catalogDigest(tools, extension),
    ...(resolved !== null
      ? { policyRevision: resolved.headRevision, policyDigest: resolved.digest }
      : {}),
  };
}

/**
 * CTL-6: a loop run that pins its engine authority (`x.pipeshub.codeTask`) and offers `code_task`. Not a guarded
 * feature: the claim reads it to keep the run on the loop, so it never leaves a run's requirements.
 */
export const CODE_TASK_TOOL_FEATURE = 'code_task_tool' as const;

/** `digestVersion` of a pin a claim verifies against what was approved (CPS); unversioned pins are not verified. */
export const DESCRIPTOR_DIGEST_VERSION = 2;
/** The worker feature a run pinning version-2 digests requires (`worker-compat`). */
export const DESCRIPTOR_PINS_FEATURE = 'descriptor_pins' as const;

/**
 * ADR-OWD-07 D-LAZY-2: a worker that lists each MCP server at the run's first use and pins it there. Admission asks
 * the agent authority for a definition that lists nothing only while a live worker advertises it, and a spec that
 * grants a server whole (`x.pipeshub.mcp[].tools: at_first_use`) requires it.
 */
export const MCP_FIRST_USE_FEATURE = 'mcp_first_use' as const;

/**
 * ADR-OWD-07 D-LAZY-5 (R31HOT2): a worker that holds a child to the first-use pins its parent carried
 * (`x.pipeshub.mcp[].pins`) and builds those tools without listing the server again. A child spec carrying one requires it.
 */
export const MCP_CARRIED_PINS_FEATURE = 'mcp_carried_pins' as const;

/** Whether a definition grants an MCP server whole (D-LAZY-2): its spec then requires `mcp_first_use`. */
export const grantsMcpAtFirstUse = (
  definition: AgentRunDefinition | null,
): boolean =>
  definition?.mcpServers.some((server) => server.atFirstUse === true) ?? false;

/**
 * A granted tool's pin: its descriptor digest when the authority bound one and
 * the run was admitted to pin them (version 2), else its identity, as before.
 */
const toolGrant = (
  id: string,
  fullName: string,
  descriptorDigest?: string | null,
): ToolGrant =>
  typeof descriptorDigest === 'string'
    ? { toolId: id, descriptorDigest, digestVersion: DESCRIPTOR_DIGEST_VERSION }
    : {
        toolId: id,
        descriptorDigest: canonicalDigest({ toolId: id, fullName }),
      };

/**
 * An MCP server's pin: the digest of the approved entries of the tools the run
 * is granted (version 2), when every one of them has one; else the names, as
 * before (nothing approved to bind them to).
 */
const mcpPin = (
  server: AgentRunDefinition['mcpServers'][number],
  kept: AgentRunDefinition['mcpServers'][number]['tools'],
  bind: boolean,
): McpPin => {
  const approved = kept.map(
    (tool) => [tool.name, tool.descriptorDigest] as const,
  );
  return bind && approved.every(([, digest]) => typeof digest === 'string')
    ? {
        instanceId: server.instanceId,
        manifestDigest: canonicalDigest(Object.fromEntries(approved)),
        digestVersion: DESCRIPTOR_DIGEST_VERSION,
      }
    : {
        instanceId: server.instanceId,
        manifestDigest: canonicalDigest({
          instanceId: server.instanceId,
          typeId: server.typeId,
          tools: kept.map((tool) => tool.fullName).sort(),
        }),
      };
};

const AT_FIRST_USE = 'at_first_use' as const;

/** Whether a pinned spec carries version-2 pins: a worker that claims it must verify them. */
export const pinsDescriptors = (spec: RunSpec): boolean =>
  spec.grants.tools.some((grant) => grant.digestVersion !== undefined) ||
  (
    (spec.extensions?.['x.pipeshub'] as PipeshubRunSpecExtension | undefined)
      ?.mcp ?? []
  ).some((server) => server.digestVersion !== undefined);

const webSearchRequested = (tools: readonly string[] | undefined): boolean =>
  tools === undefined ||
  tools.some((tool) => {
    const name = tool.trim().toLowerCase();
    return name === WEB_SEARCH_TOOL || name.startsWith(`${WEB_SEARCH_TOOL}.`);
  });

/**
 * Tool grants: the definition's tools, narrowed by the client's `tools`
 * (by fullName, as chat_stream filters) and `agentCapabilities`. A tool whose
 * identity cannot be expressed as a ToolId is left out, never widened.
 */
function grantedTools(
  definition: AgentRunDefinition,
  input: RunInputSnapshot,
  bind: boolean,
): {
  tools: RunSpec['grants']['tools'];
  mcp: PipeshubRunSpecExtension['mcp'];
} {
  const requested =
    input.tools === undefined ? null : new Set<string>(input.tools);
  const allowed = (fullName: string): boolean =>
    requested === null || requested.has(fullName);
  const tools = new Map<string, RunSpec['grants']['tools'][number]>();
  const add = (
    id: string | null,
    fullName: string,
    descriptorDigest?: string | null,
  ): boolean => {
    if (id === null) return false;
    tools.set(id, toolGrant(id, fullName, bind ? descriptorDigest : null));
    return true;
  };

  for (const toolset of definition.toolsets) {
    for (const tool of toolset.tools) {
      if (!allowed(tool.fullName)) continue;
      add(
        toolId(toolset.name, toolset.instanceId ?? 'default', tool.name),
        tool.fullName,
        tool.descriptorDigest,
      );
    }
  }
  const mcp: PipeshubRunSpecExtension['mcp'] = [];
  for (const server of definition.mcpServers) {
    if (server.atFirstUse === true) {
      // Granted whole (D-LAZY-2): the tools its first listing in the run offers, pinned by the worker then; the run's
      // tool grants name none of them, and the client's tool list cannot name what nobody has listed yet.
      if (ID.test(server.instanceId) && requested === null) {
        mcp.push({
          instanceId: server.instanceId,
          manifestDigest: canonicalDigest({
            instanceId: server.instanceId,
            typeId: server.typeId,
            tools: AT_FIRST_USE,
          }),
          tools: AT_FIRST_USE,
        });
      }
      continue;
    }
    const kept = server.tools
      .filter((tool) => allowed(tool.fullName))
      .filter((tool) =>
        add(
          toolId('mcp', server.instanceId, tool.name),
          tool.fullName,
          tool.descriptorDigest,
        ),
      );
    if (kept.length === 0 || !ID.test(server.instanceId)) continue;
    mcp.push(mcpPin(server, kept, bind));
  }
  if (
    definition.webSearch !== null &&
    input.agentCapabilities?.webSearch !== false &&
    webSearchRequested(input.tools)
  ) {
    add(
      toolId(
        'web',
        machineName(definition.webSearch, 'default'),
        WEB_SEARCH_TOOL,
      ),
      WEB_SEARCH_TOOL,
    );
  }
  return {
    tools: [...tools.values()].sort(byKey((grant) => grant.toolId)),
    mcp: mcp.sort(byKey((server) => server.instanceId)),
  };
}

const idList = (value: unknown): string[] | null =>
  Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : null;

const narrowed = (granted: string[], requested: string[] | null): string[] =>
  (requested === null
    ? granted
    : granted.filter((id) => requested.includes(id))
  )
    .filter((id) => ID.test(id))
    .sort();

type PinnedKnowledge = PipeshubRunSpecExtension['knowledge'];

/** The keys a request selects below app level by (main #4255 `SELECTION_FILTER_KEYS`). */
export const SELECTION_KEYS = [
  'recordGroups',
  'records',
  'recordsExact',
] as const;

const uniqueIds = (ids: readonly string[]): string[] =>
  [...new Set(ids.filter((id) => ID.test(id)))].sort();

/** The selection the request named, as asked: never its expansion or any access result (SYNC4 S1). */
function pinnedSelection(input: RunInputSnapshot): Partial<PinnedKnowledge> {
  const selection: Partial<PinnedKnowledge> = {};
  for (const key of SELECTION_KEYS) {
    const ids = uniqueIds(idList(input.filters[key]) ?? []);
    if (ids.length > 0) selection[key] = ids;
  }
  return selection;
}

/** A saved agent's limited sources among those pinned (S12): the worker never lets a live limit widen them. */
function pinnedSourceLimits(
  definition: AgentRunDefinition,
  pinned: ReadonlySet<string>,
): Partial<PinnedKnowledge> {
  const limits = definition.knowledge
    .filter((item) => item.limit !== undefined && pinned.has(item.connectorId))
    .map((item) => ({
      connectorId: item.connectorId,
      recordGroups: uniqueIds(item.limit?.recordGroups ?? []),
      records: uniqueIds(item.limit?.records ?? []),
    }))
    .sort(byKey((limit) => limit.connectorId));
  return limits.length > 0 ? { sourceLimits: limits } : {};
}

/**
 * Mirrors chat_stream: `filters.apps`/`filters.kb`, when present, narrow. A
 * request that selects below app level means none of a kind it leaves out,
 * never all of the agent's (main's `resolve_agent_filters`).
 */
function grantedKnowledge(
  definition: AgentRunDefinition | null,
  input: RunInputSnapshot,
): PinnedKnowledge {
  if (input.agentCapabilities?.internalSearch === false) {
    return { apps: [], kb: [], strictScope: definition !== null };
  }
  const selection = pinnedSelection(input);
  const selects = Object.keys(selection).length > 0;
  const apps = idList(input.filters['apps']) ?? (selects ? [] : null);
  const kb = idList(input.filters['kb']) ?? (selects ? [] : null);
  if (definition === null) {
    return {
      apps: narrowed(apps ?? [], null),
      kb: narrowed(kb ?? [], null),
      strictScope: false,
      ...selection,
    };
  }
  const own = (isKb: boolean): string[] =>
    definition.knowledge
      .filter((item) => item.isKb === isKb)
      .map((item) => item.connectorId);
  const knowledge = {
    apps: narrowed(own(false), apps),
    kb: narrowed(own(true), kb),
    strictScope: true,
    ...selection,
  };
  return {
    ...knowledge,
    ...pinnedSourceLimits(
      definition,
      new Set([...knowledge.apps, ...knowledge.kb]),
    ),
  };
}

/** The knowledge a run pins (`x.pipeshub.knowledge`): the built-in assistant's web mode searches none. */
export function pinnedKnowledge(
  definition: AgentRunDefinition | null,
  input: RunInputSnapshot,
): PinnedKnowledge {
  return definition === null &&
    assistantGrant(input).chat?.internalSearch === false
    ? { apps: [], kb: [], strictScope: false }
    : grantedKnowledge(definition, input);
}

/** SYNC4: the pin names a selection or an agent's source limits, which an older worker's strict model refuses. */
export const pinsKnowledgeSelection = (
  knowledge: PinnedKnowledge | undefined,
): boolean =>
  knowledge !== undefined &&
  (SELECTION_KEYS.some((key) => (knowledge[key]?.length ?? 0) > 0) ||
    (knowledge.sourceLimits?.length ?? 0) > 0);

/** SYNC4: a worker that resolves a pinned selection and source limits every attempt. */
export const KNOWLEDGE_SELECTION_FEATURE = 'knowledge_selection' as const;

type AssistantChat = NonNullable<PipeshubRunSpecExtension['chat']>;

/**
 * The built-in assistant's grant (`chat_modes/policy.py`): `internal_search`
 * searches the organization's knowledge (the client's filters narrow it) and
 * never the web; `web_search` uses the web search tools and no knowledge. The
 * client cannot add either. A run admitted in any other mode (before P1b)
 * keeps the empty grant.
 */
function assistantGrant(input: RunInputSnapshot): {
  chat?: AssistantChat;
  tools: RunSpec['grants']['tools'];
} {
  const mode = input.model.chatMode.trim().toLowerCase();
  if (!isDurableChatMode(mode, true)) return { tools: [] };
  const web = toolId('web', 'default', WEB_SEARCH_TOOL);
  const webSearch = mode === 'web_search';
  return {
    chat: {
      mode: mode as AssistantChat['mode'],
      internalSearch: mode === 'internal_search',
    },
    tools: webSearch && web !== null ? [toolGrant(web, WEB_SEARCH_TOOL)] : [],
  };
}

/**
 * `agent-code` only for a loop run admitted needing it (`agent_code`, so only a
 * worker that can run it claims it) whose pinned capabilities still prove a
 * sandbox (P5.11); otherwise `none`, never a weaker sandbox [D-3]. A run
 * admitted before requirements were recorded lists none: `none`.
 */
export function sandboxProfileFor(
  run: Pick<HarnessRun, 'requires'>,
  executor: ExecutorKind,
  capabilities: CapabilitySnapshot,
): RunSpec['grants']['sandboxProfile'] {
  return executor === 'loop' &&
    (run.requires?.features ?? []).includes('agent_code') &&
    provesCodeSandbox(capabilities.capabilities)
    ? 'agent-code'
    : 'none';
}

const activeTimeS = (run: HarnessRun): number =>
  Math.max(1, Math.ceil((run.absoluteDeadlineAt - run.createdAt) / 1000));

/** The smaller of a constant and an engine profile's limit; null is no limit. */
const within = (value: number, limit: number | null | undefined): number =>
  limit === null || limit === undefined ? value : Math.min(value, limit);

/** The four run budgets a policy fold decides (`budgets.*`, CPD-5). */
export interface RunBudgets {
  maxTurns: number;
  maxTokens: number;
  maxCostUsd: number;
  activeTimeS: number;
}

/**
 * Today's budget merge: the release budgets, a firing's cost cap and the run's
 * deadline, each under an engine profile's limit. CPD-5's shadow compares the
 * pinned `budgets.*` with it; under `CONTROL_PLANE_PIN=on` the spec takes the
 * pin's instead.
 */
export function legacyBudgets(
  profile: PinnedEngineProfile | null,
  trigger: Pick<RunTrigger, 'maxCostUsd'> | null,
  activeTimeSeconds: number,
  engineWork: boolean = profile !== null,
): RunBudgets {
  return {
    maxTurns: RUN_SPEC_BUDGETS.maxTurns,
    maxTokens: within(
      workTokens(RUN_SPEC_BUDGETS.maxTokens, engineWork),
      profile?.limits.maxTokens,
    ),
    // A firing's own cap is at most the per-run ceiling (P8a); the ledger opens the run's account from it.
    maxCostUsd: within(
      Math.min(
        RUN_SPEC_BUDGETS.maxCostUsd,
        trigger?.maxCostUsd ?? RUN_SPEC_BUDGETS.maxCostUsd,
      ),
      profile?.limits.maxUsd,
    ),
    activeTimeS: within(activeTimeSeconds, profile?.limits.maxWallS),
  };
}

/**
 * An engine run's budgets and effort under its profile (CPS NO-1..NO-5): what a code_task child inherits from its
 * loop parent never reaches past the profile it is pinned with (CTL-6), as a root engine run's never did.
 */
export function withinEngineProfile(
  spec: Pick<RunSpec, 'budgets' | 'model'>,
  profile: PinnedEngineProfile,
): Pick<RunSpec, 'budgets'> & {
  reasoningEffort: RunSpec['model']['reasoningEffort'];
} {
  const chosen = spec.model.reasoningEffort;
  const ceiling = profile.reasoningEffort ?? null;
  return {
    budgets: {
      ...spec.budgets,
      maxTokens: within(spec.budgets.maxTokens, profile.limits.maxTokens),
      maxCostUsd: within(spec.budgets.maxCostUsd, profile.limits.maxUsd),
      activeTimeS: within(spec.budgets.activeTimeS, profile.limits.maxWallS),
    },
    reasoningEffort:
      chosen === undefined || ceiling === null
        ? chosen
        : (EFFORT_RANK[chosen] as number) > (EFFORT_RANK[ceiling] as number)
          ? ceiling
          : chosen,
  };
}

/**
 * The run's reasoning effort: the request's, else the agent's default. An
 * engine profile's effort bounds both; admission already refused a request
 * above it, so only a default (a choice, not a grant) meets the bound here.
 */
function pinnedEffort(
  definition: AgentRunDefinition | null,
  input: RunInputSnapshot,
  profile: PinnedEngineProfile | null,
): RunSpec['model']['reasoningEffort'] {
  const chosen = [
    input.model.reasoningEffort,
    definition?.defaultReasoningEffort,
  ].find(
    (value): value is string =>
      typeof value === 'string' && value in EFFORT_RANK,
  ) as RunSpec['model']['reasoningEffort'];
  const ceiling = profile?.reasoningEffort ?? null;
  if (chosen === undefined || ceiling === null) return chosen;
  return (EFFORT_RANK[chosen] as number) > (EFFORT_RANK[ceiling] as number)
    ? ceiling
    : chosen;
}

/**
 * The RunSpec a claim pins, from what admission fixed: the run, the agent
 * grant and how the actor came in. Pure and deterministic, so the digest is
 * stable (P-4); every list is sorted.
 */
/**
 * The loop mode an agent definition's run executes in: the request's mode,
 * or for the system assistant definition the one legacy runs its Agent mode
 * in (`agent` is `quick`), since the input keeps the mode as the client sent it.
 */
export const pinnedLoopMode = (
  definition: Pick<AgentRunDefinition, 'agentKey'>,
  chatMode: string,
): string =>
  definition.agentKey === ASSISTANT_AGENT_DEFINITION_KEY
    ? (assistantAgentLoopMode(chatMode) ?? chatMode)
    : chatMode;

export function resolveRunSpec(
  subject: SpecSubject,
  capabilities: CapabilitySnapshot,
  executorOf: ExecutorResolver = profileExecutor,
): RunSpec {
  const { run, agentDefinition: definition } = subject;
  const trigger = subject.trigger ?? null;
  const input = run.input;
  if (input === undefined || input === null) {
    throw unresolvable('The run has no admitted input');
  }
  // CTL-6: admitted to hand off through code_task, the run is a loop run and its profile the child's authority.
  const authority =
    (run.requires?.features ?? []).includes(CODE_TASK_TOOL_FEATURE) &&
    subject.engineProfile !== undefined &&
    subject.engineProfile !== null
      ? subject.engineProfile
      : null;
  const executor = executorOf({
    agentDefinition: definition,
    input,
    engineProfile: subject.engineProfile ?? null,
    codeTask: authority !== null,
  });
  const model = pinnedModel(
    definition,
    input,
    subject.modelAccess ?? null,
    (run.requires?.features ?? []).includes(MODEL_FALLBACK_FEATURE),
  );
  const profile = authority !== null ? null : (subject.engineProfile ?? null);
  const effort = pinnedEffort(definition, input, profile);
  const assistant = definition === null ? assistantGrant(input) : null;
  const granted =
    assistant !== null
      ? { tools: assistant.tools, mcp: [] }
      : grantedTools(
          definition as AgentRunDefinition,
          input,
          // Only a run admitted to pin them is claimed by a worker that verifies them.
          (run.requires?.features ?? []).includes(DESCRIPTOR_PINS_FEATURE),
        );
  const repository =
    (executor.kind === 'engine' || authority !== null) &&
    subject.repository !== undefined &&
    subject.repository !== null
      ? pinnedRepository(subject.repository)
      : null;
  const { mcp } = granted;
  const tools = withPullRequestGrant(granted.tools, repository);

  const extension: PipeshubRunSpecExtension = {
    ...(definition !== null
      ? {
          agent: {
            agentKey: definition.agentKey,
            definitionVersion: definition.definitionVersion,
            mode: machineName(
              pinnedLoopMode(definition, input.model.chatMode),
              'auto',
            ),
          },
        }
      : {}),
    ...(assistant?.chat !== undefined ? { chat: assistant.chat } : {}),
    ...(model.model !== undefined ? { model: model.model } : {}),
    knowledge: pinnedKnowledge(definition, input),
    mcp,
    skills: (definition?.skills ?? [])
      .filter((skill) => NAME.test(skill.name))
      .map((skill) => ({
        name: skill.name,
        bundleDigest:
          skill.bundleDigest ?? legacySkillPinDigest(skill.name, skill.version),
      }))
      .sort(byKey((skill) => skill.name)),
    // The VFS arrives in P3; until then a run mounts nothing, but a turn that may hand off to an engine grants its
    // child a fork of the session workspace (CTL-6), merged back when it joins.
    mounts: authority !== null ? ['/workspace'] : [],
    via: subject.via,
    // Only a worker that reads it claims a run that carries it.
    ...(subject.surface !== undefined &&
    subject.surface !== null &&
    (run.requires?.features ?? []).includes(RUN_SURFACE_FEATURE)
      ? { surface: subject.surface }
      : {}),
    ...(subject.audience !== undefined && subject.audience !== null
      ? { audience: structuredClone(subject.audience) }
      : {}),
    ...(subject.memory !== undefined && subject.memory !== null
      ? { memory: structuredClone(subject.memory) }
      : {}),
    // Only a run that hands work to an engine works on the repository; a code_task child inherits it.
    ...(repository !== null ? { repository } : {}),
    // C20.18: the agent's environment, on a run admitted to carry it (an engine run or a code_task parent).
    ...(definition !== null &&
    (run.requires?.features ?? []).includes(ENVIRONMENT_PIN_FEATURE) &&
    pinsEnvironment(
      definition.environment,
      executor.kind === 'engine' || authority !== null,
    )
      ? { environment: structuredClone(definition.environment) }
      : {}),
    ...(authority !== null
      ? {
          codeTask: {
            profile: structuredClone(authority),
            ...(subject.codeTaskAutoRoute === false
              ? { autoRoute: false }
              : {}),
          },
        }
      : {}),
    // Only a worker that reads it claims a run that carries it; resolved here once, never parsed again.
    ...((run.requires?.features ?? []).includes(DELIVERABLE_PIN_FEATURE)
      ? { deliverable: resolveDeliverable(input.query) }
      : {}),
    // Only a worker that reads the pin claims a run that carries it.
    ...(subject.resolved !== undefined &&
    subject.resolved !== null &&
    (run.requires?.features ?? []).includes(RESOLVED_CONFIG_FEATURE)
      ? { resolved: structuredClone(subject.resolved) }
      : {}),
  };
  const releasePinned = (run.requires?.features ?? []).includes(
    RELEASE_PIN_FEATURE,
  );
  if (releasePinned) {
    extension.release = releasePin(tools, extension, subject.resolved ?? null);
  }
  const imageDigest =
    releasePinned && executor.kind === 'engine'
      ? subject.engineImages?.get(engineRef(executor.engine))
      : undefined;
  const spec: RunSpec = {
    schemaVersion: RUN_SPEC_SCHEMA_VERSION,
    harnessMajor: 2,
    foldVersion: RUN_SPEC_FOLD_VERSION,
    runKind: admittedRunKind(run, trigger !== null),
    executor,
    principalRef: principalRefFor(run.actorId),
    model: {
      policy: [model.route, ...(model.fallbacks ?? [])],
      promptVersion: RUN_SPEC_PROMPT_VERSION,
      ...(effort !== undefined ? { reasoningEffort: effort } : {}),
    },
    grants: {
      tools,
      sandboxProfile: sandboxProfileFor(run, executor.kind, capabilities),
      // Code the run executes has no network (P5.11); installs use the deployment's index.
      egress: 'none',
    },
    budgets: {
      // CPD-5 `on`: the pin's budgets, engine limits included; otherwise today's merge.
      ...(subject.resolved !== undefined && subject.resolved !== null
        ? pinnedBudgets(
            subject.resolved.effectiveConfig,
            executor.kind,
            authority !== null,
          )
        : legacyBudgets(
            profile,
            ownCostCap(trigger, subject.answered),
            activeTimeS(run),
            executor.kind === 'engine' || authority !== null,
          )),
      maxChildren: Math.max(
        definition === null
          ? RUN_SPEC_BUDGETS.maxChildren
          : (definition.maxChildren ??
              modeMaxChildren(
                pinnedLoopMode(definition, input.model.chatMode),
              )),
        // The handoff is a child run.
        authority !== null ? 1 : 0,
      ),
      calendarDeadline: new Date(run.absoluteDeadlineAt).toISOString(),
    },
    capabilitiesDigest: capabilities.digest,
    ...(imageDigest !== undefined
      ? { pins: { sandboxImage: imageDigest } }
      : {}),
    extensions: { 'x.pipeshub': extension },
    // Only a run that requires `run_gates` carries them, and only a worker that enforces them claims it.
    ...(subject.gates !== undefined && subject.gates !== null
      ? { gates: structuredClone(subject.gates) }
      : {}),
  };

  const extensionCheck = pipeshubRunSpecExtensionSchema.safeParse(extension);
  const specCheck = runSpecSchema.safeParse(spec);
  if (!extensionCheck.success || !specCheck.success) {
    const issue = (extensionCheck.error ?? specCheck.error)?.issues[0];
    throw unresolvable(
      `Resolved RunSpec is invalid: ${issue ? `${issue.path.join('.')} ${issue.message}` : 'schema mismatch'}`,
    );
  }
  if (canonicalJsonBytes(spec).length > MAX_PINNED_SPEC_BYTES) {
    throw unresolvable('Resolved RunSpec exceeds the pinned size limit');
  }
  return spec;
}

/** The repository as the spec pins it: its own fields only, in a fixed order. */
const pinnedRepository = (
  repository: SessionRepository,
): NonNullable<PipeshubRunSpecExtension['repository']> => ({
  url: repository.url,
  ...(repository.baseRef !== undefined ? { baseRef: repository.baseRef } : {}),
  toolsetInstanceId: repository.toolsetInstanceId,
});

/**
 * GIT-FLOW (GT-04): the GitHub toolset's `create_pull_request` on the repository's toolset instance joins the grant,
 * so the worker can offer `open_pull_request` (which wraps it and is approved exactly as it is). A grant the run
 * already holds is kept as it is.
 */
function withPullRequestGrant(
  tools: RunSpec['grants']['tools'],
  repository: NonNullable<PipeshubRunSpecExtension['repository']> | null,
): RunSpec['grants']['tools'] {
  const instance = repository?.toolsetInstanceId;
  if (instance === undefined) return tools;
  const id = toolId(GITHUB_TOOLSET, instance, GITHUB_PULL_REQUEST_TOOL);
  if (id === null || tools.some((grant) => grant.toolId === id)) return tools;
  return [
    ...tools,
    toolGrant(id, `${GITHUB_TOOLSET}.${GITHUB_PULL_REQUEST_TOOL}`, null),
  ].sort(byKey((grant) => grant.toolId));
}

/** The claim's resolver, pinning what `executorOf` answers (F2's seam). */
export const runSpecResolver =
  (executorOf: ExecutorResolver): RunSpecResolver =>
  (subject, capabilities) => {
    const spec = resolveRunSpec(subject, capabilities, executorOf);
    return {
      spec,
      specDigest: canonicalDigest(spec),
      capabilities: { ...capabilities.capabilities },
      capabilitiesDigest: capabilities.digest,
    };
  };

export const pinRunSpec: RunSpecResolver = runSpecResolver(profileExecutor);

/** Upgrades a pinned spec one schemaVersion (n -> n+1) on read. */
export type RunSpecUpcaster = (
  spec: Record<string, unknown>,
) => Record<string, unknown>;

/** A v2 spec predates `executor`; every v2 run ran on the loop. */
export const upcastRunSpecV2: RunSpecUpcaster = (spec) => ({
  ...spec,
  schemaVersion: 3,
  executor: spec['executor'] ?? { ...LOOP_EXECUTOR },
});

/** Keyed by the version each upcaster reads; v2 is the first spec. */
export const RUN_SPEC_UPCASTERS: Readonly<Record<number, RunSpecUpcaster>> =
  Object.freeze({ 2: upcastRunSpecV2 });

/**
 * A pinned spec as this build reads it. Older versions are upcast one step at
 * a time; a version nothing can upcast, or a newer one, refuses the claim
 * rather than run on a spec this worker does not understand (6.1.7).
 */
export function readPinnedSpec(
  stored: unknown,
  upcasters: Readonly<Record<number, RunSpecUpcaster>> = RUN_SPEC_UPCASTERS,
): RunSpec {
  const unsupported = (message: string): HarnessConflictError =>
    new HarnessConflictError('SPEC_UNSUPPORTED', message);
  if (typeof stored !== 'object' || stored === null) {
    throw unsupported('The pinned spec is not an object');
  }
  let spec = structuredClone(stored) as Record<string, unknown>;
  let version = spec['schemaVersion'];
  while (typeof version === 'number' && version < RUN_SPEC_SCHEMA_VERSION) {
    const upcast = upcasters[version];
    if (upcast === undefined) {
      throw unsupported(
        `No upcaster for RunSpec schemaVersion ${String(version)}`,
      );
    }
    spec = upcast(spec);
    if (spec['schemaVersion'] !== version + 1) {
      throw unsupported(
        `Upcaster for ${String(version)} did not advance the version`,
      );
    }
    version = spec['schemaVersion'];
  }
  const parsed = runSpecSchema.safeParse(spec);
  if (!parsed.success) {
    throw unsupported(
      `RunSpec schemaVersion ${String(version)} is not supported by this build`,
    );
  }
  return parsed.data;
}
