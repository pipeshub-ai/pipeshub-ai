import { resolvedTurnSchema, type ResolvedTurn } from '../../control-plane';
import { AuthorityClock, systemAuthorityClock } from '../domain/authority';
import { HarnessUnauthorizedError } from '../domain/errors';
import {
  MAX_RESOLVED_TURN_BYTES,
  MAX_RESOLVED_TURN_LIFETIME_S,
  RESOLVED_TURN_AUDIENCE,
  RESOLVED_TURN_ISSUER,
  RESOLVED_TURN_VERSION,
  withoutBindings,
} from '../domain/resolved-turn';
import type { ResolvedTurnMetrics } from '../ports/resolved-turn-metrics';
import { harnessLogMeta, type HarnessLogger } from './harness-log';
import type { SigningKeyring } from './signing-keyring';

/** The query service's refusal codes for a header (CPD-10 §3), by the same names. */
export type ResolvedTurnRefusal =
  | 'resolved_malformed'
  | 'resolved_algorithm'
  | 'resolved_unknown_key'
  | 'resolved_signature'
  | 'resolved_issuer'
  | 'resolved_audience'
  | 'resolved_expired'
  | 'resolved_version'
  | 'resolved_routing'
  | 'resolved_subject_mismatch'
  | 'resolved_agent_mismatch'
  | 'resolved_conversation_mismatch';

export class ResolvedTurnRefusedError extends Error {
  constructor(public readonly reason: ResolvedTurnRefusal) {
    super(`The resolved turn is refused (${reason})`);
    this.name = 'ResolvedTurnRefusedError';
  }
}

const KEYRING_REFUSALS: Readonly<
  Partial<Record<HarnessUnauthorizedError['reason'], ResolvedTurnRefusal>>
> = {
  algorithm: 'resolved_algorithm',
  unknown_key: 'resolved_unknown_key',
  signature: 'resolved_signature',
};

/** The longest token the query service reads (`MAX_RESOLVED_TURN_LENGTH`). */
const MAX_TOKEN_LENGTH = 16 * 1024;

/** The request a header is checked against: the query service's own verified identity, route and body. */
export interface ResolvedTurnRequest {
  orgId: string;
  userId: string;
  agentKey: string | null;
  conversationId: string | null;
}

export interface ResolvedTurnAuthorityOptions {
  /** The process's gateway keyring (`gatewayKeyring`): the same documents, rotation and overlap window. */
  keys: SigningKeyring;
  clock?: AuthorityClock;
  metrics?: ResolvedTurnMetrics;
  logger?: HarnessLogger;
}

/**
 * Signs `X-PipesHub-Resolved` (CPD-10 §3) with the gateway keyring under its
 * own audience. The verifier is the query service; `verify` here checks the
 * same things in the same order for Node-side checks and tests.
 */
export class ResolvedTurnAuthority {
  private readonly keys: SigningKeyring;
  private readonly clock: AuthorityClock;

  constructor(private readonly options: ResolvedTurnAuthorityOptions) {
    this.keys = options.keys;
    this.clock = options.clock ?? systemAuthorityClock;
  }

  /** Now, in the seconds a claim counts in. */
  issuedAt(): number {
    return Math.floor(this.clock.now() / 1000);
  }

  /** The claims as a compact JWS under the active key, key order as given. */
  sign(claims: ResolvedTurn): Promise<string> {
    return this.keys.sign(claims);
  }

  /** The header's value: the claims signed, without `bindings` when they would not fit (CPD10-22). */
  async header(claims: ResolvedTurn): Promise<string> {
    const token = await this.sign(claims);
    if (
      claims.bindings === undefined ||
      Buffer.byteLength(token) <= MAX_RESOLVED_TURN_BYTES
    ) {
      return token;
    }
    this.options.metrics?.trimmed();
    this.options.logger?.warn(
      'cp.resolved_turn_trimmed',
      harnessLogMeta('cp.resolved_turn_trimmed', {
        orgId: claims.org,
        actorId: claims.sub,
        agentKey: claims.agent ?? undefined,
        sessionId: claims.cid,
        pinDigest: claims.pin.digest,
        bindings: claims.bindings.length,
        bytes: Buffer.byteLength(token),
      }),
    );
    return this.sign(withoutBindings(claims));
  }

  /** The turn a token carries for `request`, or `ResolvedTurnRefusedError` with the query service's reason. */
  async verify(
    token: string | undefined,
    request?: ResolvedTurnRequest,
  ): Promise<ResolvedTurn> {
    let payload: unknown;
    try {
      payload = await this.keys.verify(token, MAX_TOKEN_LENGTH);
    } catch (error) {
      if (!(error instanceof HarnessUnauthorizedError)) throw error;
      throw new ResolvedTurnRefusedError(
        KEYRING_REFUSALS[error.reason] ?? 'resolved_malformed',
      );
    }
    const raw = (payload ?? {}) as Record<string, unknown>;
    if (raw['iss'] !== RESOLVED_TURN_ISSUER) {
      throw new ResolvedTurnRefusedError('resolved_issuer');
    }
    if (raw['aud'] !== RESOLVED_TURN_AUDIENCE) {
      throw new ResolvedTurnRefusedError('resolved_audience');
    }
    // Another version's claims are not this one's to judge.
    if ('ver' in raw && raw['ver'] !== RESOLVED_TURN_VERSION) {
      throw new ResolvedTurnRefusedError('resolved_version');
    }
    const parsed = resolvedTurnSchema.safeParse(raw);
    if (
      !parsed.success ||
      parsed.data.exp - parsed.data.iat > MAX_RESOLVED_TURN_LIFETIME_S
    ) {
      throw new ResolvedTurnRefusedError('resolved_malformed');
    }
    const turn = parsed.data;
    if (turn.exp * 1000 <= this.clock.now()) {
      throw new ResolvedTurnRefusedError('resolved_expired');
    }
    if (turn.pin.routing.path !== 'legacy') {
      throw new ResolvedTurnRefusedError('resolved_routing');
    }
    if (request === undefined) return turn;
    if (turn.org !== request.orgId || turn.sub !== request.userId) {
      throw new ResolvedTurnRefusedError('resolved_subject_mismatch');
    }
    if (turn.agent !== request.agentKey) {
      throw new ResolvedTurnRefusedError('resolved_agent_mismatch');
    }
    if (turn.cid !== request.conversationId) {
      throw new ResolvedTurnRefusedError('resolved_conversation_mismatch');
    }
    return turn;
  }
}
