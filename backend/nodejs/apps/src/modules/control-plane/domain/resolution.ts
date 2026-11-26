import type {
  CatalogToolDescriptor,
  Problem,
  ResolvedRunConfig as GeneratedResolvedRunConfig,
} from '../contracts/generated';
import type { ConfiguredLlm, ModelChoice } from './llm';
import type { Binding, Grant, Installation } from './objects';
import type { EffectiveRunConfig, PolicyLayer } from './policy-alias';
import type { ConfiguredFamilies, Routing, RoutingFacts } from './routing';

export type ToolEffect = NonNullable<CatalogToolDescriptor['effect']>;

/** Who a run is for, decided by the server (03-interfaces §4.4). */
export interface PinSubject {
  readonly orgId: string;
  readonly actorId: string;
  readonly agentKey: string | null;
  /** From the conversation record, never from the client. */
  readonly projectId: string | null;
  readonly chatMode: string;
  readonly regenerateOf?: string;
  readonly surface: 'web' | 'slack' | 'mcp' | 'schedule' | 'child' | 'next_run';
  readonly trigger?: PolicyLayer;
  readonly parent?: ResolvedRunConfig;
  readonly request: PolicyLayer;
  /**
   * The agent's definition as a layer of kind `agent`, folded under the
   * stored agent layer: the graph holds definitions until CPD-7/CPD-9 move
   * them into the control plane, so the host reads it and hands it over.
   */
  readonly agent?: PolicyLayer;
  /**
   * The user who owns the agent (its creator), from the definition admission
   * loaded: what they put on it is shared with whoever runs it (CPS).
   */
  readonly agentOwner?: string | null;
  /**
   * What the turn's route is decided on beside policy (CPD-10 §2). A child
   * names none: it takes its parent's route. A root whose host states none
   * is routed on policy alone (`NO_FACTS`).
   */
  readonly routingFacts?: RoutingFacts;
  /** The model the run asks for, as its worker looks it up; the pin's `llm` is the configured entry it finds. */
  readonly model?: ModelChoice;
  /**
   * Routes the host checked against another authority (a model account's
   * vendor rules, A1): they sit inside the configured-models ceiling though
   * the deployment's own models do not list them.
   */
  readonly verifiedRoutes?: readonly string[];
  /** A child's sub-agent role keys, nearest first (MA1): the roles whose cost ceiling bounds its model. */
  readonly roleKeys?: readonly string[];
}

/** What a turn's route reads of its subject: who, where, in which mode; never its request. */
export type RouteSubject = Pick<
  PinSubject,
  | 'orgId'
  | 'actorId'
  | 'agentKey'
  | 'projectId'
  | 'chatMode'
  | 'agentOwner'
  | 'parent'
  | 'routingFacts'
>;

export interface TeamMembership {
  readonly teamId: string;
  readonly role: string;
}

/**
 * Everything one pin reads, loaded once (02-lld §8.1): no resolver or layer
 * source performs I/O. `headRevision` is the org head the load saw;
 * `dependsOn` holds the `touchKey`s whose change makes this snapshot stale.
 */
export interface Snapshot {
  readonly orgId: string;
  readonly headRevision: number;
  /** The deployment's revision the load saw: what the deployment-wide documents (`models`) are as of. */
  readonly deploymentRevision: number;
  readonly agentKey: string | null;
  readonly actorId: string;
  readonly installations: ReadonlyMap<string, Installation>;
  /** By subject (`org`, `team:<id>`, `agent:<key>`, `user:<id>`). */
  readonly grants: ReadonlyMap<string, Grant>;
  /** By scope (`org:<id>`, `team:<id>`, `agent:<key>`, `user:<id>`). */
  readonly layers: ReadonlyMap<string, PolicyLayer>;
  /** The org's, the agent's and the actor's bindings. */
  readonly bindings: readonly Binding[];
  readonly actorTeams: readonly string[];
  readonly agentOwnerTeam: string | null;
  /** The deployment's configured models, without what reaches them. */
  readonly models: readonly ConfiguredLlm[];
  /** The family behind each configured model key, for the routing rows that list families. */
  readonly families: ConfiguredFamilies;
  /** Installation id → approved tool name → effect, from the approved descriptors (`null`: unclassified). */
  readonly toolEffects: ReadonlyMap<
    string,
    ReadonlyMap<string, ToolEffect | null>
  >;
  readonly dependsOn: ReadonlySet<string>;
}

/** A run's pin (02-lld §2.9): the generated `resolved-run-config.schema.json` type. */
export type ResolvedRunConfig = GeneratedResolvedRunConfig;

export type PinResult = ResolvedRunConfig | Problem;

/**
 * What a route or a pin was resolved on: the snapshot, with the org head and
 * the deployment's revision it was loaded at. A host hands a route's basis
 * to `pin`, and admits the pin only while both revisions still stand.
 */
export type PinBasis = Snapshot;

/** One turn's route (CPD-10 §2): `pin` on the same basis pins the same `routing` and `scope`. */
export interface RouteResult {
  readonly routing: Routing;
  readonly scope: ResolvedRunConfig['scope'];
  readonly basis: PinBasis;
  /** The `rollout.*` rows the route was decided on, as the release, the deployment and the org resolve them. */
  readonly config: EffectiveRunConfig;
}

/** What the explain view shows of a kind's Python runtime; Node never executes items (03-interfaces §3.2). */
export interface ItemRuntimeInfo {
  readonly kind: string;
  readonly supportsDriftCheck: boolean;
}
