import { z } from 'zod/v3';
import {
  AttemptKeyring,
  AttemptSigningKey,
  AuthorityClock,
  PublishedAttemptKeys,
  systemAuthorityClock,
} from '../domain/authority';
import { HarnessUnauthorizedError } from '../domain/errors';
import {
  bindingPayload,
  GATEWAY_KEY_PREFIX,
  GATEWAY_TOKEN_AUDIENCE,
  GATEWAY_TOKEN_ISSUER,
  GATEWAY_TOKEN_TTL_MS,
  GatewayClaims,
  IssuedGatewayToken,
} from '../domain/gateway-token';
import { ConfigDocumentStore } from '../ports/config-document-store';
import { generateSigningKey } from './attempt-token-codec';
import { HarnessLogger } from './harness-log';
import { SigningKeyring } from './signing-keyring';

const identifier = z.string().min(1).max(128);
const common = {
  iss: z.string(),
  aud: z.string(),
  orgId: identifier,
  sessionId: identifier,
  iat: z.number().int(),
  exp: z.number().int(),
};
// One binding: a run's attempt. Any other `bnd`, stage 1's `request` included (retired by S1R), is malformed.
const payloadSchema = z
  .object({
    ...common,
    bnd: z.literal('run'),
    runId: identifier,
    parentRunId: identifier.nullable(),
    attemptId: identifier,
    fence: z.number().int().nonnegative(),
    toolIdsDigest: identifier,
    routesDigest: identifier,
  })
  .strict();

export const generateGatewaySigningKey = (now: number): AttemptSigningKey =>
  generateSigningKey(now, GATEWAY_KEY_PREFIX);

export interface GatewayTokenAuthorityOptions {
  signingKeys: ConfigDocumentStore<AttemptKeyring>;
  publishedKeys: ConfigDocumentStore<PublishedAttemptKeys>;
  clock?: AuthorityClock;
  generateKey?: (now: number) => AttemptSigningKey;
  logger?: HarnessLogger;
}

/**
 * The gateway keyring: one per process, shared by every signer of its
 * documents (gateway tokens, resolved turns), so a rotation one of them sees
 * the others see too. The published document names the gateway profile; a
 * signer under another audience states its own in each token.
 */
export function gatewayKeyring(
  options: GatewayTokenAuthorityOptions,
): SigningKeyring {
  return new SigningKeyring(
    options.signingKeys,
    options.publishedKeys,
    {
      name: 'gateway_signing_keys',
      issuer: GATEWAY_TOKEN_ISSUER,
      audience: GATEWAY_TOKEN_AUDIENCE,
      label: 'Gateway token',
    },
    options.clock ?? systemAuthorityClock,
    options.generateKey ?? generateGatewaySigningKey,
    options.logger,
  );
}

/**
 * Signs gateway tokens (E2) with a keyring of their own: an attempt token is
 * never a gateway token, and rotating one keyring leaves the other alone.
 * Verifiers (the tool gateway in Python) read the published public keys;
 * `verify` here serves Node-side checks and tests.
 */
export class GatewayTokenAuthority {
  private readonly keys: SigningKeyring;
  private readonly clock: AuthorityClock;

  /** `keys`: the process's gateway keyring when another signer shares it (`gatewayKeyring`). */
  constructor(
    options: GatewayTokenAuthorityOptions,
    keys: SigningKeyring = gatewayKeyring(options),
  ) {
    this.clock = options.clock ?? systemAuthorityClock;
    this.keys = keys;
  }

  provision(rotateId: string | null): Promise<void> {
    return this.keys.provision(rotateId);
  }

  rotate(): Promise<void> {
    return this.keys.rotate();
  }

  async issue(claims: GatewayClaims): Promise<IssuedGatewayToken> {
    const now = this.clock.now();
    const iat = Math.floor(now / 1000);
    const exp = Math.floor((now + GATEWAY_TOKEN_TTL_MS) / 1000);
    const token = await this.keys.sign({
      iss: GATEWAY_TOKEN_ISSUER,
      aud: GATEWAY_TOKEN_AUDIENCE,
      ...bindingPayload(claims),
      iat,
      exp,
    });
    return { token, expiresAt: exp * 1000 };
  }

  /**
   * `acceptExpiredForMs`: a token that expired at most that long ago still
   * verifies (P6.5: settling a call that outlived its token; never to start one).
   */
  async verify(
    token: string | undefined,
    options: { acceptExpiredForMs?: number } = {},
  ): Promise<GatewayClaims> {
    const payload = await this.keys.verify(token);
    const issuer = (payload as { iss?: unknown } | null)?.iss;
    const audience = (payload as { aud?: unknown } | null)?.aud;
    if (issuer !== GATEWAY_TOKEN_ISSUER) {
      throw new HarnessUnauthorizedError(
        'issuer',
        'Gateway token issuer refused',
      );
    }
    if (audience !== GATEWAY_TOKEN_AUDIENCE) {
      throw new HarnessUnauthorizedError(
        'audience',
        'Gateway token audience refused',
      );
    }
    const parsed = payloadSchema.safeParse(payload);
    if (!parsed.success) {
      throw new HarnessUnauthorizedError(
        'malformed',
        'Gateway token claims are malformed',
      );
    }
    const data = parsed.data;
    if (
      data.exp * 1000 + (options.acceptExpiredForMs ?? 0) <=
      this.clock.now()
    ) {
      throw new HarnessUnauthorizedError('expired', 'Gateway token expired');
    }
    return {
      binding: 'run',
      orgId: data.orgId,
      sessionId: data.sessionId,
      runId: data.runId,
      parentRunId: data.parentRunId,
      attemptId: data.attemptId,
      fence: data.fence,
      toolIdsDigest: data.toolIdsDigest,
      routesDigest: data.routesDigest,
    };
  }
}
