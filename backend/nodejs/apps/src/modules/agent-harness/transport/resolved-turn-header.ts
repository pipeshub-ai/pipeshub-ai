import type { IncomingMessage } from 'node:http';
import type { Request, RequestHandler } from 'express';
import type { Container } from 'inversify';
import type {
  LegacyTurnHeader,
  PreparedTurn,
} from '../application/legacy-turn-header';
import type { LegacyTurn } from '../application/legacy-turn-resolver';
import { AGENT_HARNESS_LEGACY_TURN_HEADER } from '../binders/tokens';
import { RESOLVED_TURN_HEADER } from '../domain/resolved-turn';
import type { ResolvedRoute } from '../ports/routing-decider';
import { transportError } from './errors';
import { identityString } from './request-identity';

/** What a request's proxy sites need of the process's `LegacyTurnHeader`. */
export type TurnSigner = Pick<LegacyTurnHeader, 'prepare' | 'sign'>;

interface RequestTurn {
  /** The process's signer; null while none is bound (`CONTROL_PLANE_PIN` is not `on`, or the harness is not wired). */
  readonly header: () => TurnSigner | null;
  /** What the chat-turn middleware pinned before the legacy handler ran; null: no header for this turn. */
  prepared?: PreparedTurn | null;
}

/** By request: what the guard and the chat-turn middleware leave for the proxy sites of the same request. */
const turns = new WeakMap<IncomingMessage, RequestTurn>();

/**
 * Mounted before every route, in every mode: a client can never supply
 * `X-PipesHub-Resolved`. Whatever arrives is dropped, and the request is
 * given the signer its proxy sites build Node's own header with.
 */
export function resolvedTurnGuard(
  header: () => TurnSigner | null,
): RequestHandler {
  return (req, _res, next) => {
    Reflect.deleteProperty(req.headers, RESOLVED_TURN_HEADER);
    turns.set(req, { header });
    next();
  };
}

/** The guard over the harness container, which is complete only after the middleware chain is built. */
export function createResolvedTurnGuard(
  container: () => Container | undefined,
): RequestHandler {
  return resolvedTurnGuard(() => {
    const bound = container();
    return bound?.isBound(AGENT_HARNESS_LEGACY_TURN_HEADER) === true
      ? bound.get<LegacyTurnHeader>(AGENT_HARNESS_LEGACY_TURN_HEADER)
      : null;
  });
}

type Body = Record<string, unknown>;

const text = (value: unknown): string | undefined =>
  typeof value === 'string' && value.length > 0 ? value : undefined;

/** The turn of a request the legacy handler serves; undefined when it authenticates as nobody (the handler answers that). */
function legacyTurnOf(
  req: Request,
  agentKey: string | null,
): LegacyTurn | undefined {
  const user = (req as Request & { user?: Body }).user;
  const orgId = identityString(user?.['orgId']);
  const actorId = identityString(user?.['userId']);
  if (orgId === null || actorId === null) return undefined;
  const body: Body =
    typeof req.body === 'object' && req.body !== null ? (req.body as Body) : {};
  const projectId =
    req.params['conversationId'] === undefined
      ? text(body['projectId'])
      : undefined;
  return {
    orgId,
    actorId,
    agentKey,
    // Today's loop's own defaults (`chatTurnInput`).
    chatMode: text(body['chatMode']) ?? (agentKey === null ? 'quick' : 'auto'),
    ...(projectId !== undefined ? { projectId } : {}),
  };
}

/**
 * Pins a turn the legacy handler is about to serve, before it writes
 * anything, so one that cannot be pinned under `CONTROL_PLANE_PIN=on` is
 * answered 503 `RUN_CONFIG_UNAVAILABLE` and never reaches today's loop.
 * Nothing in `off` and `shadow`.
 */
export async function prepareResolvedTurn(
  req: Request,
  agentKey: string | null,
  routed?: ResolvedRoute,
): Promise<void> {
  const turn = turns.get(req);
  const signer = turn?.header() ?? null;
  const subject = legacyTurnOf(req, agentKey);
  if (turn === undefined || signer === null || subject === undefined) return;
  turn.prepared = await signer.prepare(subject, routed);
}

/** Whether this process signs the request's turn: false in `off` and `shadow`, where a legacy turn waits for nothing. */
export const signsResolvedTurn = (req: Request): boolean =>
  (turns.get(req)?.header() ?? null) !== null;

/** Where a legacy handler proxies its turn: the query route's agent and the conversation it created or continued. */
export interface LegacyProxySite {
  /** Null or absent: the built-in assistant (and Universal Agent Mode, which runs on its route). */
  agentKey?: string | null;
  conversationId: string;
}

/**
 * The headers of a chat turn proxied to the query service: the request's own
 * without any `X-PipesHub-Resolved` it carried, plus Node's signed one under
 * `CONTROL_PLANE_PIN=on`. Every proxy of a chat turn builds its headers here.
 */
export async function resolvedProxyHeaders(
  req: Request,
  site: LegacyProxySite,
): Promise<Record<string, string>> {
  const { [RESOLVED_TURN_HEADER]: _client, ...headers } = req.headers as Record<
    string,
    string
  >;
  const turn = turns.get(req);
  const signer = turn?.header() ?? null;
  if (turn === undefined || signer === null) return headers;
  const agentKey = site.agentKey ?? null;
  try {
    if (
      turn.prepared === undefined ||
      (turn.prepared !== null && turn.prepared.turn.agentKey !== agentKey)
    ) {
      await prepareResolvedTurn(req, agentKey);
    }
    const prepared = turn.prepared ?? null;
    if (prepared === null) return headers;
    return {
      ...headers,
      [RESOLVED_TURN_HEADER]: await signer.sign(prepared, site.conversationId),
    };
  } catch (error) {
    throw transportError(error);
  }
}
