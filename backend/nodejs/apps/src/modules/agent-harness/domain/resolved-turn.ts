import type {
  ResolvedRunConfig,
  ResolvedTurn,
  Routing,
} from '../../control-plane';
import {
  approvalLayers,
  readApprovalsDocument,
  WRITES_ASK,
  type FlowRule,
  type OrgApprovalRules,
} from './approvals';
import { strictestFlows } from './policy/approval-rules';

/** The header a legacy chat turn carries to the query service (CPD-10 §3). */
export const RESOLVED_TURN_HEADER = 'x-pipeshub-resolved';
export const RESOLVED_TURN_ISSUER = 'pipeshub-node/agent-harness';
/** Its own audience: a gateway token never verifies as a resolved turn, nor the reverse. */
export const RESOLVED_TURN_AUDIENCE = 'pipeshub-query/resolved-turn';
export const RESOLVED_TURN_VERSION = 1;
export const RESOLVED_TURN_TTL_S = 60;
/** A verifier refuses a longer-lived token as one the issuer did not mint. */
export const MAX_RESOLVED_TURN_LIFETIME_S = 120;
/** A header over this is sent without its `bindings` (CPD10-22): proxies and servers cap a header line near here. */
export const MAX_RESOLVED_TURN_BYTES = 8192;

const ROLLOUT = 'rollout.';

/**
 * What today's loop applies for a layer nobody can read (QA10-F1): it holds
 * no approval rule but the flow rules, so it counts such a layer as one that
 * asks from the lowest threshold.
 */
export const UNREADABLE_FLOW: FlowRule = { level: 'ask', above: 'public' };

/**
 * Whether today's readers cannot read a layer a run of (org, agent) narrows
 * through: the query service's `UNREADABLE_LAYER`. A pin holds such a layer
 * as rules that ask for writes, which an operator's own `writes: ask` also
 * is, so the pin alone cannot say it; the approvals document can. `edition`:
 * the edition's own rule layers there (`OrgApprovalRules`), null once the
 * policy store is their record or where an edition keeps none.
 */
export function holdsUnreadableLayer(
  approvalsDocument: unknown,
  orgId: string,
  agentKey: string | null,
  edition: OrgApprovalRules | null = null,
): boolean {
  return approvalLayers(
    readApprovalsDocument(approvalsDocument),
    orgId,
    agentKey,
    edition,
  ).includes(WRITES_ASK);
}

/** Who a legacy turn is for and where, as the query service will verify it against its own request. */
export interface ResolvedTurnSubject {
  /** The org and user the proxied request authenticates as. */
  orgId: string;
  actorId: string;
  /** The query route's agent; null for the built-in assistant. */
  agentKey: string | null;
  /** The conversation the handler created or continued. */
  conversationId: string;
  /** Seconds since the epoch. */
  issuedAt: number;
  /** Why the turn is on today's loop. */
  routing: Routing;
  /** The agent directory holds a current "may run" for the actor. */
  mayRun?: boolean;
  /** `holdsUnreadableLayer` for the turn. */
  unreadableApprovals?: boolean;
}

const flowOf = (pin: ResolvedRunConfig): FlowRule[] => {
  const approvals = pin.effectiveConfig.dimensions['approvals']?.value as
    { flow?: unknown } | null | undefined;
  return Array.isArray(approvals?.flow) ? (approvals.flow as FlowRule[]) : [];
};

/** The owners the query service keeps a credential under: the executing user or a service-account agent. */
const CREDENTIAL_OWNER = /^(?:user|agent):/;

/**
 * The claims of `X-PipesHub-Resolved` for one legacy turn: a projection of
 * its pin, in the key order the shared vector states. `stages` holds every
 * boolean `rollout.*` row (the routing lists are not switches); `bindings`
 * the ready, bound items a user or an agent owns (the org's own credentials
 * are read as before). Never a secret: bindings by reference.
 */
export function resolvedTurnClaims(
  pin: ResolvedRunConfig,
  turn: ResolvedTurnSubject,
): ResolvedTurn {
  const stages: Record<string, boolean> = {};
  for (const [path, row] of Object.entries(pin.effectiveConfig.dimensions)) {
    if (path.startsWith(ROLLOUT) && typeof row.value === 'boolean') {
      stages[path.slice(ROLLOUT.length)] = row.value;
    }
  }
  const bindings = pin.effectiveSet.items.flatMap((item) =>
    item.state === 'ready' &&
    item.binding !== null &&
    CREDENTIAL_OWNER.test(item.binding.owner)
      ? [
          {
            installation: item.installation,
            owner: item.binding.owner,
            ref: item.binding.ref,
          },
        ]
      : [],
  );
  return {
    iss: RESOLVED_TURN_ISSUER,
    aud: RESOLVED_TURN_AUDIENCE,
    iat: turn.issuedAt,
    exp: turn.issuedAt + RESOLVED_TURN_TTL_S,
    ver: RESOLVED_TURN_VERSION,
    org: turn.orgId,
    sub: turn.actorId,
    agent: turn.agentKey,
    cid: turn.conversationId,
    pin: {
      digest: pin.digest,
      head: pin.headRevision,
      routing: { path: turn.routing.path, reason: turn.routing.reason },
      scope: {
        agentKey: pin.scope.agentKey,
        projectId: pin.scope.projectId,
        ...(pin.scope.agentOwner !== undefined
          ? { agentOwner: pin.scope.agentOwner }
          : {}),
      },
    },
    stages,
    flow: strictestFlows([
      ...flowOf(pin),
      ...(turn.unreadableApprovals === true ? [UNREADABLE_FLOW] : []),
    ]),
    ...(turn.mayRun === true ? { access: { run: true as const } } : {}),
    ...(bindings.length > 0 ? { bindings } : {}),
  };
}

/** The claims without `bindings`: an installation not named is read as before. */
export function withoutBindings(claims: ResolvedTurn): ResolvedTurn {
  const { bindings: _bindings, ...rest } = claims;
  return rest;
}
