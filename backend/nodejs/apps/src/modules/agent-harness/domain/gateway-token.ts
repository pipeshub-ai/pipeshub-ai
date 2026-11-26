/**
 * Gateway tokens (E2, ARCHITECTURE §4.4.2 and §4.8): what the egress proxy
 * presents to the run-scoped tool gateway on a sandbox's behalf. Signed by a
 * keyring of their own (not the attempt-token keyring), 10-minute TTL, bound to
 * one attempt of one run with the grant it was pinned with. Stage 1's request
 * binding was retired by S1R. Pure types and rules.
 */
import { canonicalDigest } from '../contracts/canonical-json';
import type { PriorityClass } from './priority-class';
import { pipeshubRunSpecExtensionSchema } from '../contracts/generated/ext/pipeshub/run-spec';
import { RunSpec } from '../contracts/generated/core/run-spec';
import {
  pinnedRoleRoutes,
  type EngineRole,
  type RoleRoute,
} from './model-roles';

export const GATEWAY_TOKEN_ISSUER = 'pipeshub-node/agent-harness';
export const GATEWAY_TOKEN_AUDIENCE = 'pipeshub-agent-harness/gateway';
export const GATEWAY_TOKEN_TTL_MS = 10 * 60 * 1000;
/** Gateway signing keys are named `hgk_…`, attempt keys `hak_…`. */
export const GATEWAY_KEY_PREFIX = 'hgk_';

export interface RunGatewayClaims {
  binding: 'run';
  orgId: string;
  sessionId: string;
  runId: string;
  /** The run that spawned this one; null for a run no parent admitted. */
  parentRunId: string | null;
  attemptId: string;
  fence: number;
  toolIdsDigest: string;
  routesDigest: string;
}

export type GatewayClaims = RunGatewayClaims;

export interface IssuedGatewayToken {
  token: string;
  /** Wall-clock ms. */
  expiresAt: number;
}

/** Order and duplicates do not matter; equals Python's `grant_digest`. */
export function grantDigest(ids: readonly string[]): string {
  return canonicalDigest([...new Set(ids)].sort());
}

/**
 * A pinned model route's id in a gateway token's routes digest (E3), and the
 * item a child's `access` grant names (A1): a pinned model account is part of
 * it, so a token and a child are bound to the account too. Equals Python's
 * `PinnedRoute.route_id`.
 */
export function modelRouteId(route: {
  provider: string;
  model: string;
  access?: { accountKind: string; accountId?: string };
}): string {
  const base = `${route.provider}:${route.model}`;
  const accountId = route.access?.accountId;
  return accountId === undefined
    ? base
    : `${base}@${route.access?.accountKind ?? ''}:${accountId}`;
}

/**
 * The grant a run's gateway token binds: its pinned tool ids and its model
 * routes (E3: the model gateway checks the routes it serves against this
 * digest; equals Python's `route_digest`).
 */
export function gatewayGrantDigests(spec: RunSpec): {
  toolIdsDigest: string;
  routesDigest: string;
} {
  return {
    toolIdsDigest: grantDigest(spec.grants.tools.map((grant) => grant.toolId)),
    routesDigest: grantDigest(spec.model.policy.map(modelRouteId)),
  };
}

/**
 * What the model gateway may call for one run (E3): the pinned routes (with a
 * pinned model account, A1), budget and effort, and who acts from which engine
 * (`loop` for a loop run), so the gateway can check the vendor rules and that a
 * user's key is the actor's own.
 */
export interface RunModelGrant {
  runId: string;
  attemptId: string;
  routesDigest: string;
  routes: Array<{
    provider: string;
    model: string;
    modelKey?: string;
    modelName?: string;
    account?: { kind: string; accountId: string };
    pays?: string;
    /** G-48 (WP-100): the failures that move the engine's model here from the route before it; alternates only. */
    on?: string[];
  }>;
  /**
   * GA-15: the engine roles the pin maps to their own routes (by provider and
   * model; the gateway serves each from the configured model that lists it).
   * A role left out calls the first route. Absent: every role does.
   */
  roleRoutes?: Partial<Record<EngineRole, RoleRoute>>;
  reasoningEffort?: string;
  budgets: { maxCostUsd: number; maxTokens: number };
  actorId: string;
  engine: string;
  /** The run's priority class (G-45): the gateway serves a provider's limited slots to higher classes first. */
  priorityClass: PriorityClass;
}

/**
 * What a run's pin grants the model gateway: its routes as the gateway reads
 * them (the configured model on the first, a pinned model account and who
 * pays, A1) and the engine that calls (`loop` for a loop run).
 */
export function grantedRoutes(
  spec: RunSpec,
): Pick<RunModelGrant, 'routes' | 'engine' | 'roleRoutes'> {
  const roleRoutes = pinnedRoleRoutes(spec);
  const extension = pipeshubRunSpecExtensionSchema.safeParse(
    spec.extensions?.['x.pipeshub'],
  );
  const configured = extension.success ? extension.data.model : undefined;
  return {
    routes: spec.model.policy.map((route, index) => ({
      provider: route.provider,
      model: route.model,
      ...(index === 0 && configured !== undefined
        ? { modelKey: configured.modelKey, modelName: configured.modelName }
        : {}),
      ...(route.access?.accountId !== undefined
        ? {
            account: {
              kind: route.access.accountKind,
              accountId: route.access.accountId,
            },
          }
        : {}),
      ...(route.access !== undefined ? { pays: route.access.pays } : {}),
      ...(index > 0 && route.on !== undefined && route.on.length > 0
        ? { on: [...route.on] }
        : {}),
    })),
    engine:
      spec.executor?.kind === 'engine' ? spec.executor.engine.kind : 'loop',
    ...(Object.keys(roleRoutes).length > 0 ? { roleRoutes } : {}),
  };
}

/** The token's payload claims for a binding, in the order the wire format writes them. */
export function bindingPayload(claims: GatewayClaims): Record<string, unknown> {
  return {
    bnd: 'run',
    orgId: claims.orgId,
    sessionId: claims.sessionId,
    runId: claims.runId,
    parentRunId: claims.parentRunId,
    attemptId: claims.attemptId,
    fence: claims.fence,
    toolIdsDigest: claims.toolIdsDigest,
    routesDigest: claims.routesDigest,
  };
}
