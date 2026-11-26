import type { ResolvedRunConfig } from '../../control-plane';
import { runPolicyFields } from './run-policy-fields';
import { PinnedModelAccess } from './model-access';
import type { RunKeyDigest } from './run-key-digest';
import { modelRouteId } from './gateway-token';
import { attributionAuditFields } from './principal';
import { createHash } from 'node:crypto';
import { RELEASE_DEFAULTS } from '../contracts/generated/core/policy-data';
import {
  canonicalDigest,
  canonicalJsonBytes,
} from '../contracts/canonical-json';
import { Labels } from '../contracts/generated/core/labels';
import { RunSpec, runSpecSchema } from '../contracts/generated/core/run-spec';
import {
  PipeshubRunSpecExtension,
  pipeshubRunSpecExtensionSchema,
} from '../contracts/generated/ext/pipeshub/run-spec';
import { HarnessConflictError } from './errors';
import { inheritedGates } from './run-gates';
import { ENVIRONMENT_PIN_FEATURE } from './agent-environment';
import type {
  ChildOf,
  HarnessRun,
  RunInputSnapshot,
  RunStatus,
  RunTransition,
} from './execution';
import type { JournalEventInput } from './journal';
import { inlineContentRef } from './run-content';
import { priorityClassOf, priorityFields } from './priority-class';
import {
  CHILD_ASKS_FEATURE,
  CHILD_MODELS_FEATURE,
  INHERITED_GRANTS_FEATURE,
  INHERITED_LABELS_FEATURE,
  VENDOR_SIGNIN_FEATURE,
  type WorkerFeature,
} from './worker-compat';
import type { PipeshubTurnLabel } from '../contracts/generated/ext/pipeshub';
import {
  EngineProfileNarrowing,
  engineExecutor,
  narrowProfile,
  PinnedEngineProfile,
} from './engine-profiles';
import {
  catalogDigest,
  DELIVERABLE_PIN_FEATURE,
  DESCRIPTOR_PINS_FEATURE,
  KNOWLEDGE_SELECTION_FEATURE,
  engineAlternates,
  RELEASE_PIN_FEATURE,
  RESOLVED_CONFIG_FEATURE,
  LOOP_EXECUTOR,
  MCP_CARRIED_PINS_FEATURE,
  MCP_FIRST_USE_FEATURE,
  PinnedRunSpec,
  pinsDescriptors,
  pinsKnowledgeSelection,
  principalRefFor,
  RunExecutor,
  RunVia,
  withinEngineProfile,
} from './run-spec';

/** REPORT 6.9: a child of a child of a child is the deepest run. */
export const CHILD_MAX_DEPTH = 3;
/** The most children an agent definition may grant one run. */
export const MAX_CHILDREN_PER_RUN = RELEASE_DEFAULTS.children.maxPerRun;

export const CHILD_REFUSAL_CODES = [
  'CHILDREN_NOT_GRANTED',
  'CHILD_LIMIT_REACHED',
  'CHILD_GRANT_TOO_LARGE',
  'CHILD_DEPTH_EXCEEDED',
  'CHILD_BUDGET_EXCEEDS_REMAINING',
  'CHILD_OPERATOR_STOPPED',
] as const;
export type ChildRefusalCode = (typeof CHILD_REFUSAL_CODES)[number];

export type SandboxProfile = RunSpec['grants']['sandboxProfile'];
export type Mount = PipeshubRunSpecExtension['mounts'][number];

/**
 * What a parent asks its child to be granted. An omitted dimension inherits
 * the parent's value; a named one must fit inside it (P6.3).
 */
export interface ChildGrantRequest {
  tools?: string[];
  sandboxProfile?: SandboxProfile;
  egress?: string;
  mounts?: Mount[];
  knowledge?: { apps?: string[]; kb?: string[] };
  skills?: string[];
  /** The parent's pinned access routes the child may use, by route id (A1, `modelRouteId`). */
  access?: string[];
  maxTurns?: number;
  /** A share of the parent's tokens (K-L's interim rule until P6.5's ledger). */
  maxTokens?: number;
  maxChildren?: number;
}

/** A child's grant once every dimension was checked against its parent's. */
export interface ChildGrants {
  tools: string[];
  sandboxProfile: SandboxProfile;
  egress: string;
  mounts: Mount[];
  knowledge: { apps: string[]; kb: string[] };
  skills: string[];
  access: string[];
  maxTurns: number;
  maxTokens: number;
  maxChildren: number;
}

export interface ChildGrantRefusal {
  code: Extract<
    ChildRefusalCode,
    'CHILD_GRANT_TOO_LARGE' | 'CHILD_DEPTH_EXCEEDED'
  >;
  dimension: keyof ChildGrants | 'executor';
  /** The requested items the parent does not hold, sorted. */
  items: string[];
}

export type ChildGrantDecision =
  | { grants: ChildGrants; refusal: null }
  | { grants: null; refusal: ChildGrantRefusal };

/** The product extension; a core spec (an embedding host's) has none and holds nothing there. */
export const pipeshub = (spec: RunSpec): PipeshubRunSpecExtension | undefined =>
  spec.extensions?.['x.pipeshub'] as PipeshubRunSpecExtension | undefined;

const sortedUnique = <T extends string>(values: readonly T[]): T[] =>
  [...new Set(values)].sort();

/** The requested subset of `held` (or of what `alsoHeld` admits), or the items neither holds. */
const subset = <T extends string>(
  held: readonly T[],
  requested: readonly T[] | undefined,
  alsoHeld: (item: string) => boolean = () => false,
): { granted: T[]; extra: T[] } => {
  if (requested === undefined)
    return { granted: sortedUnique(held), extra: [] };
  const own = new Set<string>(held);
  const holds = (item: string): boolean => own.has(item) || alsoHeld(item);
  const asked = sortedUnique(requested);
  return {
    granted: asked.filter(holds),
    extra: asked.filter((item) => !holds(item)),
  };
};

type McpGrant = PipeshubRunSpecExtension['mcp'][number];

const MCP_TOOL_ID = /^mcp@([^/]+)\/(.+)$/;

/**
 * R31-HOT: a child's grant of every tool of an MCP server its parent holds whole (`tools: at_first_use`, ADR-OWD-07
 * D-LAZY-2). Such a server's tools are named by nobody before its first listing, so the parent's tool grants hold
 * none of them: the server whole is held as this one id, and a named tool of it as `mcp@<instance>/<tool>`.
 */
export const wholeMcpToolId = (instanceId: string): string =>
  `mcp@${instanceId}/*`;

const WHOLE_TOOL = '*';

/** The MCP servers a spec holds whole, by instance: what it holds beyond its tool grants. */
const wholeMcpServers = (spec: RunSpec): Map<string, McpGrant> =>
  new Map(
    (pipeshub(spec)?.mcp ?? [])
      .filter((server) => server.tools === 'at_first_use')
      .map((server) => [server.instanceId, server]),
  );

/**
 * ADR-OWD-07 D-LAZY-5 (R31HOT2): the first-use pins of the MCP servers a parent has listed, as its worker carries them
 * into a spawn: per instance, each tool its first listing served, by the server's own name, at its descriptor digest.
 */
export type McpFirstUsePins = Readonly<
  Record<string, Readonly<Record<string, string>>>
>;

/**
 * The parent's pin of one server: the one its own spec carries (inherited from its parent, which its worker is held
 * to), else the one its worker pinned at first use. Node trusts its own pinned spec over a worker's report.
 */
const parentPinOf = (
  server: McpGrant,
  carried: McpFirstUsePins | undefined,
): Readonly<Record<string, string>> | undefined =>
  server.pins ?? carried?.[server.instanceId];

/**
 * The tool ids a spec holds (ADR-OWD-07 D-LAZY-8): its tool grants, and for a server it holds whole the server
 * (`wholeMcpToolId`) or any one of its tools. The one answer to "does the parent hold this tool" for every child.
 * Once the parent has listed a server it holds whole, it holds exactly what that listing served (D-LAZY-5): a tool
 * outside its pin is not the parent's, so no child is granted it.
 */
const heldTools = (
  spec: RunSpec,
  carried?: McpFirstUsePins,
): { granted: string[]; alsoHeld: (toolId: string) => boolean } => {
  const whole = wholeMcpServers(spec);
  return {
    granted: [
      ...spec.grants.tools.map((grant) => grant.toolId),
      ...[...whole.keys()].map(wholeMcpToolId),
    ],
    alsoHeld: (toolId) => {
      const [, instanceId, tool] = MCP_TOOL_ID.exec(toolId) ?? [];
      const server =
        instanceId === undefined ? undefined : whole.get(instanceId);
      if (server === undefined || tool === undefined) return false;
      const pin = parentPinOf(server, carried);
      return pin === undefined || Object.hasOwn(pin, tool);
    },
  };
};

/** `pin` narrowed to `names` (sorted keys); undefined when none of them is pinned. */
const pinOf = (
  pin: Readonly<Record<string, string>> | undefined,
  names: readonly string[] | null,
): Record<string, string> | undefined => {
  if (pin === undefined) return undefined;
  const kept = Object.entries(pin)
    .filter(([tool]) => names === null || names.includes(tool))
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return kept.length > 0 ? Object.fromEntries(kept) : undefined;
};

/** `none`, or exactly the parent's value: one level is never another's subset. */
const noneOrSame = (held: string, requested: string | undefined): string[] =>
  requested === undefined || requested === 'none' || requested === held
    ? []
    : [requested];

const atMost = (held: number, requested: number | undefined): string[] =>
  requested === undefined || requested <= held ? [] : [String(requested)];

interface DimensionInput {
  parent: RunSpec;
  request: ChildGrantRequest;
  /** The child's depth (a root run is 0). */
  depth: number;
  /** The parent's first-use pins its worker carried (R31HOT2); absent: none listed, or an older worker. */
  pins?: McpFirstUsePins;
}

/** One dimension's check: what it grants, or what it refuses. */
type GrantDimension = (input: DimensionInput) =>
  | { granted: Partial<ChildGrants>; refused?: undefined }
  | {
      granted?: undefined;
      refused: { items: string[]; code?: ChildGrantRefusal['code'] };
    };

const refuse = (
  items: string[],
  code?: ChildGrantRefusal['code'],
): ReturnType<GrantDimension> => ({
  refused: { items, ...(code !== undefined ? { code } : {}) },
});

/**
 * The ⊆ rule per dimension, in the order a refusal is reported. Adding a
 * dimension (A1's access routes held by the parent) is adding an entry.
 */
const CHILD_GRANT_DIMENSIONS: ReadonlyArray<
  [keyof ChildGrants, GrantDimension]
> = [
  [
    'tools',
    ({ parent, request, pins }) => {
      const held = heldTools(parent, pins);
      const { granted, extra } = subset(
        held.granted,
        request.tools,
        held.alsoHeld,
      );
      return extra.length > 0 ? refuse(extra) : { granted: { tools: granted } };
    },
  ],
  [
    'sandboxProfile',
    ({ parent, request }) => {
      const extra = noneOrSame(
        parent.grants.sandboxProfile,
        request.sandboxProfile,
      );
      return extra.length > 0
        ? refuse(extra)
        : {
            granted: {
              sandboxProfile:
                request.sandboxProfile ?? parent.grants.sandboxProfile,
            },
          };
    },
  ],
  [
    'egress',
    ({ parent, request }) => {
      const extra = noneOrSame(parent.grants.egress, request.egress);
      return extra.length > 0
        ? refuse(extra)
        : { granted: { egress: request.egress ?? parent.grants.egress } };
    },
  ],
  [
    'mounts',
    ({ parent, request }) => {
      const { granted, extra } = subset(
        pipeshub(parent)?.mounts ?? [],
        request.mounts,
      );
      return extra.length > 0
        ? refuse(extra)
        : { granted: { mounts: granted } };
    },
  ],
  [
    'knowledge',
    ({ parent, request }) => {
      const held = pipeshub(parent)?.knowledge ?? { apps: [], kb: [] };
      const apps = subset(held.apps, request.knowledge?.apps);
      const kb = subset(held.kb, request.knowledge?.kb);
      const extra = [
        ...apps.extra.map((id) => `app:${id}`),
        ...kb.extra.map((id) => `kb:${id}`),
      ];
      return extra.length > 0
        ? refuse(extra)
        : { granted: { knowledge: { apps: apps.granted, kb: kb.granted } } };
    },
  ],
  [
    'skills',
    ({ parent, request }) => {
      const { granted, extra } = subset(
        (pipeshub(parent)?.skills ?? []).map((skill) => skill.name),
        request.skills,
      );
      return extra.length > 0
        ? refuse(extra)
        : { granted: { skills: granted } };
    },
  ],
  [
    'access',
    ({ parent, request }) => {
      const { granted, extra } = subset<string>(
        parent.model.policy.map(modelRouteId),
        request.access,
      );
      return extra.length > 0
        ? refuse(extra)
        : { granted: { access: granted } };
    },
  ],
  [
    'maxTurns',
    ({ parent, request }) => {
      const extra = atMost(parent.budgets.maxTurns, request.maxTurns);
      return extra.length > 0
        ? refuse(extra)
        : {
            granted: { maxTurns: request.maxTurns ?? parent.budgets.maxTurns },
          };
    },
  ],
  [
    'maxTokens',
    ({ parent, request }) => {
      const extra = atMost(parent.budgets.maxTokens, request.maxTokens);
      return extra.length > 0
        ? refuse(extra)
        : {
            granted: {
              maxTokens: request.maxTokens ?? parent.budgets.maxTokens,
            },
          };
    },
  ],
  [
    'maxChildren',
    ({ parent, request, depth }) => {
      const wanted = request.maxChildren ?? 0;
      if (wanted > 0 && depth >= CHILD_MAX_DEPTH) {
        return refuse([String(wanted)], 'CHILD_DEPTH_EXCEEDED');
      }
      const extra = atMost(parent.budgets.maxChildren, wanted);
      return extra.length > 0
        ? refuse(extra)
        : { granted: { maxChildren: wanted } };
    },
  ],
];

/**
 * grant(child) ⊆ grant(parent) (REPORT 6.9, P6.3): pure. A widening request
 * is refused with the first dimension that widens, never narrowed silently.
 */
export function childGrants(
  parent: RunSpec,
  request: ChildGrantRequest,
  depth: number,
  /** The parent's first-use pins (R31HOT2): a server it listed holds only the tools that listing served. */
  pins?: McpFirstUsePins,
): ChildGrantDecision {
  const grants: Partial<ChildGrants> = {};
  for (const [dimension, check] of CHILD_GRANT_DIMENSIONS) {
    const result = check({
      parent,
      request,
      depth,
      ...(pins !== undefined ? { pins } : {}),
    });
    if (result.refused !== undefined) {
      return {
        grants: null,
        refusal: {
          code: result.refused.code ?? 'CHILD_GRANT_TOO_LARGE',
          dimension,
          items: result.refused.items,
        },
      };
    }
    Object.assign(grants, result.granted);
  }
  return { grants: grants as ChildGrants, refusal: null };
}

/** What a child asks to run on (E1); absent: the loop. */
export interface ChildExecutorRequest {
  kind: 'engine';
  /** Narrows the parent's engine authority; absent: takes it whole. */
  profile?: EngineProfileNarrowing;
}

export type ChildExecutorDecision =
  | { executor: RunExecutor; refusal: null }
  | { executor: null; refusal: ChildGrantRefusal };

/**
 * The ⊆ rule's `executor` dimension (E1): an engine child narrows its
 * parent's engine authority, an engine parent's pinned profile or, for a loop
 * parent, `authority` (the profile the deployment resolves for the parent's
 * org); with neither, or a narrowing that widens it, the
 * child is refused naming `executor`, never narrowed silently.
 */
export function childExecutor(
  parent: RunSpec,
  request: ChildExecutorRequest | undefined,
  authority: PinnedEngineProfile | null = null,
): ChildExecutorDecision {
  if (request === undefined) {
    return { executor: { ...LOOP_EXECUTOR }, refusal: null };
  }
  const refusal = (items: string[]): ChildExecutorDecision => ({
    executor: null,
    refusal: { code: 'CHILD_GRANT_TOO_LARGE', dimension: 'executor', items },
  });
  const pinned =
    parent.executor?.kind === 'engine'
      ? (parent.executor.engine.profile ?? null)
      : authority;
  if (pinned === null) return refusal(['engine']);
  const decision =
    request.profile === undefined
      ? { profile: pinned, refusal: null }
      : narrowProfile(pinned, request.profile);
  if (decision.refusal !== null) {
    return refusal([decision.refusal.dimension ?? 'profile']);
  }
  return { executor: engineExecutor(decision.profile), refusal: null };
}

export const childRefused = (
  code: ChildRefusalCode,
  message: string,
): HarnessConflictError => new HarnessConflictError(code, message);

/**
 * The child's run id, derived from who asked: the run index is then the
 * idempotency key of `parentRunId:invocationId` (REPORT 6.9).
 */
export function childRunIdFor(
  orgId: string,
  parentRunId: string,
  invocationId: string,
): string {
  const hex = createHash('sha256')
    .update(canonicalJsonBytes(['child-run', orgId, parentRunId, invocationId]))
    .digest('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

/** The digest `child.spawned` names: everything the child was granted. */
export const childGrantDigest = (spec: RunSpec): `sha256:${string}` => {
  const extension = pipeshub(spec);
  return canonicalDigest({
    grants: spec.grants,
    knowledge: extension?.knowledge ?? null,
    mcp: extension?.mcp ?? [],
    skills: extension?.skills ?? [],
    mounts: extension?.mounts ?? [],
    budgets: {
      maxTurns: spec.budgets.maxTurns,
      maxTokens: spec.budgets.maxTokens,
      maxChildren: spec.budgets.maxChildren,
    },
  });
};

type ToolGrant = RunSpec['grants']['tools'][number];

/**
 * The child's MCP pins and the tool grants they add (ADR-OWD-07 D-LAZY-8): a server the child holds whole stays whole; a
 * server the parent holds whole of which the child was granted named tools is pinned to exactly those (never wider),
 * their grants bound to the parent's whole pin; a server the parent pinned by its tools follows the tools the child
 * keeps. A server the parent has listed carries the parent's first-use pin narrowed to what the child holds (D-LAZY-5,
 * R31HOT2): the child sees exactly the tools its parent saw, and does not list the server again.
 */
function childMcp(
  servers: readonly McpGrant[],
  granted: ReadonlySet<string>,
  carried?: McpFirstUsePins,
): { mcp: McpGrant[]; tools: ToolGrant[] } {
  const named = new Map<string, string[]>();
  for (const toolId of granted) {
    const [, instanceId, tool] = MCP_TOOL_ID.exec(toolId) ?? [];
    if (instanceId === undefined || tool === undefined || tool === WHOLE_TOOL)
      continue;
    named.set(instanceId, [...(named.get(instanceId) ?? []), tool]);
  }
  const mcp: McpGrant[] = [];
  const tools: ToolGrant[] = [];
  const withPin = (
    entry: McpGrant,
    pin: Record<string, string> | undefined,
  ): McpGrant => {
    const { pins: _parents, ...rest } = structuredClone(entry);
    return pin !== undefined ? { ...rest, pins: pin } : rest;
  };
  for (const server of servers) {
    const names = (named.get(server.instanceId) ?? []).sort();
    const pin = parentPinOf(server, carried);
    if (server.tools !== 'at_first_use') {
      if (names.length > 0) mcp.push(withPin(server, pinOf(pin, names)));
      continue;
    }
    if (granted.has(wholeMcpToolId(server.instanceId))) {
      mcp.push(withPin(server, pinOf(pin, null)));
      continue;
    }
    if (names.length === 0) continue;
    mcp.push(
      withPin(
        {
          instanceId: server.instanceId,
          manifestDigest: canonicalDigest({
            instanceId: server.instanceId,
            wholeOf: server.manifestDigest,
            tools: names,
          }),
        },
        pinOf(pin, names),
      ),
    );
    for (const name of names) {
      const toolId = `mcp@${server.instanceId}/${name}`;
      tools.push({
        toolId,
        descriptorDigest: canonicalDigest({
          toolId,
          wholeOf: server.manifestDigest,
        }),
      });
    }
  }
  return { mcp, tools };
}

/** What a child runs as, in the product's terms (K-L: deep mode's sub-agent definition). */
export type SubAgentDefinition = NonNullable<
  PipeshubRunSpecExtension['subAgent']
>;

/** A child's workspace fork as its spec pins it (P6.4). */
export type ChildWorkspacePin = NonNullable<
  PipeshubRunSpecExtension['workspace']
>;

/**
 * What a worker needs to run a child: it completes with its result
 * (`child_runs`), as a sub-agent it is assembled from its pinned definition
 * (`deep_mode`, K-L), with a pinned fork it works on that fork
 * (`workspace_forks`, P6.4), an engine child runs on the engine kernel
 * (`executor_engine`, E1), and a loop child keeping its parent's code sandbox
 * runs `run_code` with its hooks and receipts (`agent_code`, P5.11).
 */
export const childFeatures = (
  pinned: PinnedRunSpec,
  /** A sub-agent role set its model: `child.spawned.model` is on the session's stream (MA1). */
  roleModel = false,
): WorkerFeature[] => {
  const extension = pipeshub(pinned.spec);
  return [
    'child_runs',
    ...(roleModel ? [CHILD_MODELS_FEATURE] : []),
    ...(pinned.spec.executor?.kind === 'engine'
      ? (['executor_engine'] as const)
      : []),
    ...(extension?.subAgent !== undefined ? (['deep_mode'] as const) : []),
    ...(extension?.workspace !== undefined
      ? (['workspace_forks'] as const)
      : []),
    // An older worker's strict spec model refuses the field (UX2).
    ...(extension?.inheritedLabel !== undefined
      ? ([INHERITED_LABELS_FEATURE] as const)
      : []),
    // An older worker's strict spec model refuses the field (FLOWASK).
    ...(extension?.inheritedGrants !== undefined
      ? ([INHERITED_GRANTS_FEATURE] as const)
      : []),
    // An older worker's strict spec model refuses the field (ANSWER-CONTRACT-FU-3).
    ...(extension?.parentAsks !== undefined ? [CHILD_ASKS_FEATURE] : []),
    ...(pinned.spec.executor?.kind !== 'engine' &&
    pinned.spec.grants.sandboxProfile === 'agent-code'
      ? (['agent_code'] as const)
      : []),
    ...(pinsDescriptors(pinned.spec) ? [DESCRIPTOR_PINS_FEATURE] : []),
    // An older worker's strict spec model refuses a server granted whole (D-LAZY-2), which a child inherits whole.
    ...(extension?.mcp.some((server) => server.tools === 'at_first_use')
      ? [MCP_FIRST_USE_FEATURE]
      : []),
    // An older worker's strict spec model refuses a carried first-use pin (R31HOT2).
    ...(extension?.mcp.some((server) => server.pins !== undefined)
      ? [MCP_CARRIED_PINS_FEATURE]
      : []),
    // An older worker's strict spec model refuses the field (C20.18).
    ...(extension?.environment !== undefined ? [ENVIRONMENT_PIN_FEATURE] : []),
    // Only a worker that reads the pin claims a child whose spec carries one.
    ...(extension?.resolved !== undefined
      ? ([RESOLVED_CONFIG_FEATURE] as const)
      : []),
    ...(pinsKnowledgeSelection(extension?.knowledge)
      ? ([KNOWLEDGE_SELECTION_FEATURE] as const)
      : []),
    // An older worker's strict spec model refuses the field (C2.07).
    ...(extension?.release !== undefined
      ? ([RELEASE_PIN_FEATURE] as const)
      : []),
    // An older worker's strict spec model refuses the field (ADR-RUN-01 D1).
    ...(extension?.deliverable !== undefined
      ? ([DELIVERABLE_PIN_FEATURE] as const)
      : []),
    // SIGNGAP: a worker without the sign-in add-on refuses it in the engine (`signin_unavailable`).
    ...(pinned.spec.model.policy.some(
      (layer) => layer.access?.route === 'vendor_sandbox_login',
    )
      ? [VENDOR_SIGNIN_FEATURE]
      : []),
  ];
};

/** What the parent needs from then on: it joins (`child_runs`) and merges a forked child (P6.4). */
export const parentFeatures = (
  child: PinnedRunSpec,
  roleModel = false,
): WorkerFeature[] => [
  'child_runs',
  ...(pipeshub(child.spec)?.workspace !== undefined
    ? (['workspace_forks'] as const)
    : []),
  ...(roleModel ? [CHILD_MODELS_FEATURE] : []),
];

/** What later runs of the session need once the spawn is on its stream (SS1). */
export const sessionChildFeatures = (roleModel = false): WorkerFeature[] =>
  roleModel ? ['child_runs', CHILD_MODELS_FEATURE] : ['child_runs'];

/**
 * The child's spec, pinned at its admission: the parent's pinned spec with
 * the checked grants, as a child (`runKind: child`) on the loop. Principal,
 * model, capabilities, deadline and how the actor came in are the parent's;
 * `subAgent` and `workspace` are the admission's (never the parent's), and
 * the parent's history `audience` is not passed on.
 */
/**
 * Whether an engine child needs its access route decided now (A1): an engine
 * runs only on a pinned access route, and a loop parent's routes pin none.
 */
export function childNeedsAccess(
  parent: RunSpec,
  grants: ChildGrants,
  executor: RunExecutor,
): boolean {
  if (executor.kind !== 'engine') return false;
  const access = new Set(grants.access);
  return parent.model.policy
    .filter((route) => access.has(modelRouteId(route)))
    .some((route) => route.access === undefined);
}

/** The ⊆ rule on a decided route: its id must be one the child was granted. */
export function childAccessRefusal(
  grants: Pick<ChildGrants, 'access'>,
  decided: PinnedModelAccess,
): ChildGrantRefusal | null {
  const id = modelRouteId({ ...decided.route, access: decided.access });
  return grants.access.includes(id)
    ? null
    : { code: 'CHILD_GRANT_TOO_LARGE', dimension: 'access', items: [id] };
}

/**
 * ANSWER-CONTRACT-FU-3 (ADR-RUN-03 D2): whether a run can put a question to a person, and so whether a child it
 * hands work to may ask one, which the child's admission pins (`x.pipeshub.parentAsks`): a person's run, or a child
 * of one. A schedule's or a webhook's firing (`scheduled`) has nobody watching, so its jobs never wait on an answer.
 */
export const parentCanAsk = (spec: RunSpec): boolean =>
  spec.runKind === 'interactive' ||
  (spec.runKind === 'child' && pipeshub(spec)?.parentAsks === true);

/** A role's model as a child's spec pins it: its route and the configured entry that serves it. */
export interface ChildSpecModel {
  provider: string;
  model: string;
  modelKey: string;
}

export function childRunSpec(
  parent: PinnedRunSpec,
  grants: ChildGrants,
  subAgent?: SubAgentDefinition,
  workspace?: ChildWorkspacePin,
  executor: RunExecutor = LOOP_EXECUTOR,
  /** The access route admission decided for an engine child of a loop parent (A1, E1). */
  modelAccess?: PinnedModelAccess,
  /** The parent's label when it asked for the child (UX2): the child's context starts from it. */
  inheritedLabel?: PipeshubTurnLabel,
  /** The child's own pin when a worker reads it (CPD-6); absent, its spec carries none, never its parent's. */
  resolved?: PipeshubRunSpecExtension['resolved'],
  /** The model the child's role set (MA1): its one route, never a fallback to another. Absent: its parent's. */
  model?: ChildSpecModel,
  /** The engine state a code_task child starts from (LCFIX), as admission found it; absent: none. */
  engineCarry?: PipeshubRunSpecExtension['engineCarry'],
  /** The conversation grants the parent held when it asked (FLOWASK); absent or empty: none pinned. */
  inheritedGrants?: PipeshubRunSpecExtension['inheritedGrants'],
  /**
   * An engine child's run image digest as the broker reported it at admission (C2.20); pinned only when the
   * parent pinned its release (`release_pin`). Absent: the child pins no image.
   */
  imageDigest?: string,
  /** Whether the parent can ask a person (ANSWER-CONTRACT-FU-3, `parentCanAsk`); absent: not pinned. */
  parentAsks?: boolean,
  /** The parent's first-use pins its worker carried (R31HOT2, ADR-OWD-07 D-LAZY-5); absent: none. */
  mcpPins?: McpFirstUsePins,
): PinnedRunSpec {
  const {
    pins: _parentsPins,
    gates: parentGates,
    ...spec
  } = structuredClone(parent.spec);
  // G-03, G-04: a child is held to its parent's dry run and plan gate (a helper never acts for a dry run); the
  // parent's verify gate checks the parent's own answer, never the child's.
  const childGates = inheritedGates(parentGates, null);
  const extension = pipeshub(spec);
  if (
    extension === undefined &&
    (subAgent !== undefined || workspace !== undefined)
  ) {
    throw new HarnessConflictError(
      'SPEC_UNRESOLVABLE',
      'A sub-agent definition or workspace fork needs the parent spec to carry the product extension',
    );
  }
  const tools = new Set(grants.tools);
  const servers = childMcp(extension?.mcp ?? [], tools, mcpPins);
  const skills = new Set(grants.skills);
  let childExtension: PipeshubRunSpecExtension | undefined;
  if (extension !== undefined) {
    // A child renders only its own stream, so the parent's history audience (SS2) has nothing to filter.
    const {
      subAgent: _inheritedAgent,
      workspace: _inheritedWorkspace,
      audience: _inheritedAudience,
      inheritedLabel: _parentsOwnInheritance,
      inheritedGrants: _parentsOwnGrants,
      parentAsks: _parentsOwnParent,
      resolved: _parentsPin,
      // A child never hands off again: the engine authority is its parent turn's (CTL-6).
      codeTask: _parentsAuthority,
      engineCarry: _parentsCarry,
      repository: parentsRepository,
      deliverable: parentsDeliverable,
      environment: parentsEnvironment,
      ...rest
    } = extension;
    childExtension = {
      ...rest,
      // GIT-FLOW: the parent's repository is its engine child's (code_task); a loop child never touches it.
      ...(parentsRepository !== undefined && executor.kind === 'engine'
        ? { repository: structuredClone(parentsRepository) }
        : {}),
      // ADR-RUN-01 D1: the engine child the work is handed to owes what its parent was pinned to owe; a helper does
      // not.
      ...(parentsDeliverable !== undefined && executor.kind === 'engine'
        ? { deliverable: structuredClone(parentsDeliverable) }
        : {}),
      // C20.18: the engine child the work is handed to sets up its parent's agent's environment; a helper does not.
      ...(parentsEnvironment !== undefined && executor.kind === 'engine'
        ? { environment: structuredClone(parentsEnvironment) }
        : {}),
      // The parent's selection and source limits, unchanged: a child narrows apps/kb only, never widens (SYNC4 S2).
      knowledge: {
        ...extension.knowledge,
        apps: grants.knowledge.apps,
        kb: grants.knowledge.kb,
      },
      mcp: servers.mcp,
      skills: extension.skills.filter((skill) => skills.has(skill.name)),
      mounts: grants.mounts,
      ...(subAgent !== undefined
        ? { subAgent: structuredClone(subAgent) }
        : {}),
      ...(workspace !== undefined
        ? { workspace: structuredClone(workspace) }
        : {}),
      ...(inheritedLabel !== undefined
        ? { inheritedLabel: structuredClone(inheritedLabel) }
        : {}),
      ...(inheritedGrants !== undefined && inheritedGrants.length > 0
        ? { inheritedGrants: structuredClone(inheritedGrants) }
        : {}),
      ...(resolved !== undefined
        ? { resolved: structuredClone(resolved) }
        : {}),
      ...(engineCarry !== undefined
        ? { engineCarry: structuredClone(engineCarry) }
        : {}),
      ...(parentAsks !== undefined ? { parentAsks } : {}),
    };
  }
  const access = new Set(grants.access);
  let policy = spec.model.policy.filter((route) =>
    access.has(modelRouteId(route)),
  );
  // The configured model names the parent's first route; a child without it has none.
  if (childExtension !== undefined && policy[0] !== spec.model.policy[0]) {
    delete childExtension.model;
  }
  if (model !== undefined) {
    policy = [{ provider: model.provider, model: model.model }];
    if (childExtension !== undefined) {
      childExtension.model = {
        modelKey: model.modelKey,
        modelName: model.model,
      };
    }
  }
  const granted = policy;
  if (modelAccess !== undefined) {
    // The decided route is one the child was granted (childEngineAccess checks it): pinned as admission pins.
    // G-48 (WP-100): the parent's alternates it was granted follow it, reached through the Model Gateway as its
    // own model is; a model its role sets has none of the parent's.
    const decided = `${modelAccess.route.provider}:${modelAccess.route.model}`;
    const alternates =
      model === undefined
        ? engineAlternates(
            modelAccess.access,
            granted.filter(
              (route) =>
                (route.on?.length ?? 0) > 0 &&
                `${route.provider}:${route.model}` !== decided,
            ),
          )
        : [];
    policy = [
      { ...modelAccess.route, access: { ...modelAccess.access } },
      ...alternates,
    ];
    if (childExtension !== undefined) {
      if (modelAccess.model !== undefined) {
        childExtension.model = { ...modelAccess.model };
      } else {
        delete childExtension.model;
      }
    }
  }
  const budgets = {
    ...spec.budgets,
    maxTurns: grants.maxTurns,
    maxTokens: grants.maxTokens,
    maxChildren: grants.maxChildren,
  };
  // An engine child stays inside its profile, whatever its loop parent was pinned with (CTL-6).
  const bounded =
    executor.kind === 'engine' && executor.engine.profile !== undefined
      ? withinEngineProfile(
          { budgets, model: spec.model },
          executor.engine.profile,
        )
      : null;
  const kept = spec.grants.tools.filter((grant) => tools.has(grant.toolId));
  const childTools =
    servers.tools.length === 0
      ? kept
      : [...kept, ...servers.tools].sort((a, b) =>
          a.toolId < b.toolId ? -1 : a.toolId > b.toolId ? 1 : 0,
        );
  if (childExtension?.release !== undefined) {
    // The child's own catalog and, when it has one, its own policy pin; else its parent's, which it runs under.
    const own = resolved as
      { headRevision?: unknown; digest?: unknown } | undefined;
    childExtension.release = {
      ...childExtension.release,
      catalogDigest: catalogDigest(childTools, childExtension),
      ...(typeof own?.headRevision === 'number' &&
      typeof own.digest === 'string'
        ? { policyRevision: own.headRevision, policyDigest: own.digest }
        : {}),
    };
  }
  const pinsImage =
    imageDigest !== undefined &&
    executor.kind === 'engine' &&
    childExtension?.release !== undefined;
  const child: RunSpec = {
    ...spec,
    runKind: 'child',
    executor: structuredClone(executor),
    model: {
      ...spec.model,
      policy,
      ...(bounded?.reasoningEffort !== undefined
        ? { reasoningEffort: bounded.reasoningEffort }
        : {}),
    },
    grants: {
      tools: childTools,
      sandboxProfile: grants.sandboxProfile,
      egress: grants.egress,
    },
    budgets: bounded?.budgets ?? budgets,
    ...(pinsImage ? { pins: { sandboxImage: imageDigest } } : {}),
    ...(childExtension !== undefined
      ? { extensions: { ...spec.extensions, 'x.pipeshub': childExtension } }
      : {}),
    ...(childGates !== undefined ? { gates: childGates } : {}),
  };
  const specCheck = runSpecSchema.safeParse(child);
  const extensionCheck =
    childExtension === undefined
      ? null
      : pipeshubRunSpecExtensionSchema.safeParse(childExtension);
  if (!specCheck.success || extensionCheck?.success === false) {
    const issue = (specCheck.error ?? extensionCheck?.error)?.issues[0];
    throw new HarnessConflictError(
      'SPEC_UNRESOLVABLE',
      `Child RunSpec is invalid: ${issue ? `${issue.path.join('.')} ${issue.message}` : 'schema mismatch'}`,
    );
  }
  return {
    spec: child,
    specDigest: canonicalDigest(child),
    capabilities: { ...parent.capabilities },
    capabilitiesDigest: parent.capabilitiesDigest,
  };
}

const nodeEvent = (
  type: string,
  data: Record<string, unknown>,
): JournalEventInput => ({
  type,
  schemaVersion: 1,
  critical: true,
  visibility: 'owner',
  data,
});

/**
 * The child stream's first event: a child acts as its parent's principal,
 * started by its parent's initiator and on its parent's authority (OWD-2),
 * whatever the spawn asked for.
 */
export const childAdmittedEvent = (
  parent: Pick<HarnessRun, 'runId' | 'actorId' | 'attribution'>,
  via: RunVia,
): JournalEventInput => ({
  ...nodeEvent('run.admitted', {
    runKind: 'child',
    principalRef: principalRefFor(parent.actorId),
    parentRunId: parent.runId,
    via,
  }),
  ...(parent.attribution !== undefined
    ? { auditFields: attributionAuditFields(parent.attribution) }
    : {}),
});

export const childSpawnedEvent = (
  childRunId: string,
  invocationId: string,
  grantDigest: string,
  taskId?: string,
  /** Written only when a role set the child's model (MA1); absent: the child calls its parent's. */
  model?: { provider: string; model: string; role: string },
): JournalEventInput =>
  nodeEvent('child.spawned', {
    childRunId,
    invocationId,
    ...(taskId !== undefined ? { taskId } : {}),
    grantDigest,
    ...(model !== undefined
      ? {
          model: {
            provider: model.provider,
            model: model.model,
            role: model.role,
          },
        }
      : {}),
  });

/** What a child's worker hands back with its completion: its result document and label. */
export interface ChildResult {
  resultRef: { ref: string; hash: string; size: number };
  labels: Labels;
}

/** How a child ended, as the transaction ending it knows. */
export interface ChildEnding {
  status: Extract<RunStatus, 'completed' | 'failed' | 'cancelled'>;
  outcome?: RunTransition['outcome'];
  errorType?: string;
  /** With `errorType` `out_of_memory`: the sandbox's memory limit in MB, which the job card names (R32SECCOMP-FU-4). */
  memoryLimitMb?: number;
  /** The worker's result, on a completion only. */
  result?: ChildResult;
}

const MACHINE_NAME = /^[a-z0-9][a-z0-9_.-]{0,63}$/;

/**
 * The sub-agent result shape (`spawn_agent.child_result_content`) for a child
 * that produced no answer: Node writes it inline, as trusted content of its own.
 */
export function childFailureResult(
  errorType: string,
  memoryLimitMb?: number,
): ChildResult {
  // An out-of-memory end names its limit, so the parent's step and model say which limit the work met.
  const document = {
    error: errorType,
    ...(memoryLimitMb !== undefined ? { memoryLimitMb } : {}),
    output: '',
    success: false,
  };
  return {
    resultRef: inlineContentRef(canonicalJsonBytes(document)),
    labels: { integrity: 'trusted' },
  };
}

/** `child.completed` for the parent's stream; Node's failure result for any ending but an answer. */
export function childCompletedEvent(
  child: Pick<HarnessRun, 'runId'>,
  ending: ChildEnding,
): JournalEventInput {
  const answered = ending.status === 'completed' && ending.result !== undefined;
  const errorType = MACHINE_NAME.test(ending.errorType ?? '')
    ? (ending.errorType as string)
    : ending.status === 'cancelled'
      ? 'cancelled'
      : ending.status === 'completed'
        ? 'child_result_missing'
        : 'worker_failed';
  const result =
    answered && ending.result !== undefined
      ? ending.result
      : childFailureResult(
          errorType,
          errorType === OUT_OF_MEMORY_ERROR ? ending.memoryLimitMb : undefined,
        );
  const outcome = answered
    ? ending.outcome === 'needs_input'
      ? 'needs_input'
      : 'answered'
    : ending.status === 'cancelled'
      ? 'cancelled'
      : 'failed';
  return nodeEvent('child.completed', {
    childRunId: child.runId,
    resultRef: result.resultRef,
    labels: result.labels,
    outcome,
    ...(answered ? {} : { errorType }),
    ...(!answered &&
    errorType === OUT_OF_MEMORY_ERROR &&
    ending.memoryLimitMb !== undefined
      ? { memoryLimitMb: ending.memoryLimitMb }
      : {}),
  });
}

/** The code a run ends with when its sandbox's memory limit ended its work (R31GVISO-FU-3). */
const OUT_OF_MEMORY_ERROR = 'out_of_memory';

const memoryLimit = (value: unknown): { memoryLimitMb?: number } =>
  typeof value === 'number' &&
  Number.isInteger(value) &&
  value > 0 &&
  value <= 1_048_576
    ? { memoryLimitMb: value }
    : {};

/** The parent's wake when its last open child ended (REPORT 6.1.3: `suspended → queued: child done`). */
export const childDoneResumedEvent = (): JournalEventInput =>
  nodeEvent('run.resumed', { cause: 'child_done' });

/** The parent is waiting on its children and the last of them just ended. */
export const joinWakes = (
  parent: Pick<HarnessRun, 'status' | 'suspendedFor' | 'children'>,
  openAfter: number,
): boolean =>
  openAfter === 0 &&
  parent.status === 'paused' &&
  parent.suspendedFor === 'join';

export const JOIN_READY = 'JOIN_READY';

/** A `join` suspension with no open child would never be woken. */
export function assertJoinWaits(
  run: Pick<HarnessRun, 'children'>,
  transition: Pick<RunTransition, 'detail'> | undefined,
): void {
  const detail = transition?.detail;
  if (detail?.kind !== 'suspend' || detail.reason !== 'join') return;
  if ((run.children?.open ?? 0) === 0) {
    throw new HarnessConflictError(
      JOIN_READY,
      'Every child of the run has ended; it has nothing to wait for',
    );
  }
}

/** `childResult` belongs on a child's completion, and only there. */
export function assertChildResult(
  run: Pick<HarnessRun, 'parent'>,
  transition:
    (Pick<RunTransition, 'status'> & { childResult?: ChildResult }) | undefined,
): void {
  if (transition === undefined) return;
  const child = (run.parent ?? null) !== null;
  if (!child && transition.childResult !== undefined) {
    throw new HarnessConflictError(
      'INVALID_COMMIT',
      'Only a child run completes with a child result',
    );
  }
  if (
    child &&
    transition.status === 'completed' &&
    transition.childResult === undefined
  ) {
    throw new HarnessConflictError(
      'INVALID_COMMIT',
      'A child run completes with its result',
    );
  }
}

export const childOfRun = (
  parent: Pick<HarnessRun, 'runId' | 'parent'>,
  invocationId: string,
): ChildOf => ({
  runId: parent.runId,
  invocationId,
  depth: (parent.parent?.depth ?? 0) + 1,
});

/** `budgets.maxChildren` bounds the children a run admits over its life. */
export function assertChildBound(
  parent: Pick<HarnessRun, 'children'>,
  maxChildren: number,
): void {
  if (maxChildren <= 0) {
    throw childRefused(
      'CHILDREN_NOT_GRANTED',
      'The run is not granted child runs',
    );
  }
  if ((parent.children?.spawned ?? 0) >= maxChildren) {
    throw childRefused(
      'CHILD_LIMIT_REACHED',
      'The run admitted every child it is granted',
    );
  }
}

/** What a store writes for a new child (its requirements are the store's to add). */
export interface ChildRunSubject {
  childRunId: string;
  invocationId: string;
  requestHash: string;
  input: RunInputSnapshot;
  /** The child's pin (CPD-5); its run document keeps what Node applies of it (C4.10). */
  resolved?: ResolvedRunConfig;
}

/**
 * A child run as admitted: queued and dispatched, in its parent's session as
 * its parent's actor, with its parent's deadline. Its user message key names
 * no conversation message: a child never writes the conversation.
 */
export function childRunDocument(
  parent: HarnessRun,
  subject: ChildRunSubject,
  now: number,
  runKeys: Pick<RunKeyDigest, 'digest'>,
): HarnessRun {
  return {
    runId: subject.childRunId,
    orgId: parent.orgId,
    sessionId: parent.sessionId,
    actorId: parent.actorId,
    // A child is its parent's principal (OWD-2), in its parent's role: it reaches only that person's workspaces (OWD-16).
    ...(parent.sessionRole !== undefined
      ? { sessionRole: parent.sessionRole }
      : {}),
    ...(parent.attribution !== undefined
      ? { attribution: { ...parent.attribution } }
      : {}),
    ...(parent.agentKey !== undefined ? { agentKey: parent.agentKey } : {}),
    userMessageKey: `child:${subject.childRunId}`,
    idempotencyKey: runKeys.digest(
      parent.orgId,
      `child:${parent.runId}:${subject.invocationId}`,
    ),
    payloadHash: subject.requestHash,
    harnessMajor: 2,
    profile: 'dispatched',
    status: 'queued',
    outcome: null,
    // A child serves its parent: it waits as long as the parent would (G-45).
    ...priorityFields(priorityClassOf(parent)),
    revision: 0,
    fence: 0,
    attemptId: null,
    workerId: null,
    leaseExpiresAt: null,
    absoluteDeadlineAt: parent.absoluteDeadlineAt,
    ...runPolicyFields(subject.resolved),
    checkpoint: null,
    attempts: 0,
    notBefore: now,
    checkpointRef: null,
    input: structuredClone(subject.input),
    parent: childOfRun(parent, subject.invocationId),
    createdAt: now,
    updatedAt: now,
  };
}

/** The parent's wake hint: one per (parent, last child), so a retried transaction writes it once. */
export const joinWakeCommand = (
  parentRunId: string,
  childRunId: string,
): { commandId: string; runId: string; kind: 'run_resume' } => ({
  commandId: `join:${parentRunId}:${childRunId}`,
  runId: parentRunId,
  kind: 'run_resume',
});

/** How a worker's terminal transition ends a child. */
export const endingOf = (
  transition: Pick<
    RunTransition,
    'status' | 'outcome' | 'errorType' | 'childResult' | 'detail'
  >,
): ChildEnding => ({
  status: transition.status as ChildEnding['status'],
  ...(transition.outcome !== undefined ? { outcome: transition.outcome } : {}),
  ...(transition.errorType !== undefined
    ? { errorType: transition.errorType }
    : {}),
  ...(transition.detail?.kind === 'fail'
    ? memoryLimit(transition.detail.memoryLimitMb)
    : {}),
  ...(transition.childResult !== undefined
    ? { result: transition.childResult }
    : {}),
});

/** How Node itself ends a child (the sweeper, `failRun`, a cancel). */
export const sweptEnding = (
  status: RunStatus,
  eventData: Record<string, unknown>,
): ChildEnding => ({
  status: status as ChildEnding['status'],
  errorType:
    typeof eventData['errorType'] === 'string'
      ? eventData['errorType']
      : status === 'failed'
        ? 'deadline_exceeded'
        : 'cancelled',
});
