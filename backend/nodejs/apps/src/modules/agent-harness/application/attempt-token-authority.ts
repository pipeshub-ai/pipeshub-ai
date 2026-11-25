import { z } from 'zod/v3';
import {
  ATTEMPT_TOKEN_AUDIENCE,
  ATTEMPT_TOKEN_ISSUER,
  ATTEMPT_TOKEN_SLACK_MS,
  AttemptClaims,
  AttemptKeyring,
  AttemptSigningKey,
  AuthorityClock,
  IssuedAttemptToken,
  MAX_LEASE_DURATION_MS,
  PublishedAttemptKeys,
  systemAuthorityClock,
} from '../domain/authority';
import { HarnessUnauthorizedError } from '../domain/errors';
import { ConfigDocumentStore } from '../ports/config-document-store';
import { generateAttemptSigningKey } from './attempt-token-codec';
import { HarnessLogger } from './harness-log';
import { SigningKeyring } from './signing-keyring';

const identifier = z.string().min(1).max(128);

const payloadSchema = z
  .object({
    iss: z.string(),
    aud: z.string(),
    orgId: identifier,
    runId: identifier,
    attemptId: identifier,
    fence: z.number().int().nonnegative(),
    workerId: identifier,
    iat: z.number().int(),
    exp: z.number().int(),
  })
  .strict();

const refuse = (
  reason: ConstructorParameters<typeof HarnessUnauthorizedError>[0],
  message: string,
): HarnessUnauthorizedError => new HarnessUnauthorizedError(reason, message);

export interface AttemptTokenAuthorityOptions {
  signingKeys: ConfigDocumentStore<AttemptKeyring>;
  publishedKeys: ConfigDocumentStore<PublishedAttemptKeys>;
  clock?: AuthorityClock;
  generateKey?: (now: number) => AttemptSigningKey;
  logger?: HarnessLogger;
}

/**
 * Signs and verifies attempt tokens with a harness-only Ed25519 keyring. The
 * private keys live only in the encrypted signing-keys document; public keys
 * are published separately for verifiers that must not mint.
 */
export class AttemptTokenAuthority {
  private readonly keys: SigningKeyring;
  private readonly clock: AuthorityClock;

  constructor(options: AttemptTokenAuthorityOptions) {
    this.clock = options.clock ?? systemAuthorityClock;
    this.keys = new SigningKeyring(
      options.signingKeys,
      options.publishedKeys,
      {
        name: 'attempt_signing_keys',
        issuer: ATTEMPT_TOKEN_ISSUER,
        audience: ATTEMPT_TOKEN_AUDIENCE,
        label: 'Attempt token',
      },
      this.clock,
      options.generateKey ?? generateAttemptSigningKey,
      options.logger,
    );
  }

  /** Creates the keyring once; rotates on a new operator id or an aged key. */
  provision(rotateId: string | null): Promise<void> {
    return this.keys.provision(rotateId);
  }

  rotate(): Promise<void> {
    return this.keys.rotate();
  }

  async issue(
    claims: AttemptClaims,
    leaseDurationMs: number,
  ): Promise<IssuedAttemptToken> {
    const now = this.clock.now();
    const lease = Math.min(Math.max(leaseDurationMs, 0), MAX_LEASE_DURATION_MS);
    const exp = Math.floor((now + lease + ATTEMPT_TOKEN_SLACK_MS) / 1000);
    const token = await this.keys.sign({
      iss: ATTEMPT_TOKEN_ISSUER,
      aud: ATTEMPT_TOKEN_AUDIENCE,
      orgId: claims.orgId,
      runId: claims.runId,
      attemptId: claims.attemptId,
      fence: claims.fence,
      workerId: claims.workerId,
      iat: Math.floor(now / 1000),
      exp,
    });
    return { token, expiresAt: exp * 1000 };
  }

  /** Signature, issuer, audience and expiry; the caller matches the claims to the request. */
  async verify(token: string | undefined): Promise<AttemptClaims> {
    const payload = payloadSchema.safeParse(await this.keys.verify(token));
    if (!payload.success) {
      throw refuse('malformed', 'Attempt token claims are malformed');
    }
    const { iss, aud, exp, iat: _iat, ...claims } = payload.data;
    if (iss !== ATTEMPT_TOKEN_ISSUER) {
      throw refuse('issuer', 'Attempt token issuer refused');
    }
    if (aud !== ATTEMPT_TOKEN_AUDIENCE) {
      throw refuse('audience', 'Attempt token audience refused');
    }
    if (exp * 1000 <= this.clock.now()) {
      throw refuse('expired', 'Attempt token expired');
    }
    return claims;
  }
}
