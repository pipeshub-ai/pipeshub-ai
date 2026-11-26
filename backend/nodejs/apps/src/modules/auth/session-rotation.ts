import { createHash, randomBytes } from 'crypto';
import jwt from 'jsonwebtoken';
import { UnauthorizedError } from '../../libs/errors/http.errors';
import {
  getSessionFamilyRepository,
  SessionFamily,
  SessionFamilyRepository,
} from '../../libs/repositories/session-family';
import { refreshTokenJwtGenerator } from '../../libs/utils/createJwt';

/**
 * Refresh-token rotation with reuse detection (ADR-WEB-02 D9, R43AUTH-FU-1).
 *
 * A sign-in starts a session family. Each refresh replaces the family's token with a new one, so a copied token stops
 * working once its owner refreshes. A token presented after it was replaced is a copy in someone else's hands (or the
 * owner's, when the copy refreshed first): either way the family ends, and both have to sign in again.
 *
 * Two tabs, or a retry after a lost answer, can present the token just replaced within a moment of the rotation. That
 * is not a copy: within `REFRESH_REUSE_GRACE_MS` the caller gets the family's current token instead.
 */
export const REFRESH_REUSE_GRACE_MS = 60_000;

/** The token id a family adopted from a refresh token issued before rotation (it has no `jti`). */
export const LEGACY_TOKEN_ID = 'legacy';

export const REFRESH_REUSE_REASON = 'refresh_reuse';

export const SIGNED_OUT_REASON = 'signed_out';

const SESSION_GONE = 'Your session is no longer valid. Please sign in again.';

export const SESSION_REPLAYED =
  'This session was used from another place after it was renewed, so it has been ended. Please sign in again.';

/** A replaced refresh token came back after its grace: the family is ended. */
export class SessionReuseDetected extends UnauthorizedError {
  constructor(
    readonly familyId: string,
    /** Whether this request ended the family (false when an earlier replay already had). */
    readonly endedNow: boolean,
  ) {
    super(SESSION_REPLAYED);
  }
}

export interface RefreshClaims {
  userId: string;
  orgId: string;
  sid?: unknown;
  jti?: unknown;
  exp?: unknown;
}

export type RotationOutcome =
  'started' | 'rotated' | 'grace' | 'adopted' | 'kept';

export interface RotatedSession {
  refreshToken: string;
  familyId: string;
  outcome: RotationOutcome;
}

const newId = (): string => randomBytes(18).toString('base64url');

/** A token from before rotation names no family; its own hash does, so a second present of it is found. */
export const legacyFamilyId = (token: string): string =>
  `legacy-${createHash('sha256').update(token, 'utf8').digest('hex').slice(0, 40)}`;

const seconds = (at: Date): number => Math.floor(at.getTime() / 1000);

export class SessionRotation {
  constructor(
    private readonly secret: () => string,
    private readonly repo: () => SessionFamilyRepository = getSessionFamilyRepository,
    private readonly clock: () => Date = () => new Date(),
  ) {}

  /** A sign-in: a new family and its first refresh token. */
  async start(userId: string, orgId: string): Promise<RotatedSession> {
    const familyId = newId();
    const tokenId = newId();
    // The lifetime is the one REFRESH_TOKEN_EXPIRY sets; read it back from a token signed with it.
    const probe = refreshTokenJwtGenerator(userId, orgId, this.secret(), {
      sid: familyId,
      jti: tokenId,
    });
    const exp = (
      JSON.parse(
        Buffer.from(probe.split('.')[1] ?? '', 'base64url').toString('utf8'),
      ) as { exp?: number }
    ).exp;
    if (typeof exp !== 'number') throw new Error('refresh token has no expiry');
    await this.repo().start({
      orgId,
      familyId,
      userId,
      currentTokenId: tokenId,
      previousTokenId: null,
      rotatedAt: null,
      expiresAt: new Date(exp * 1000),
    });
    return { refreshToken: probe, familyId, outcome: 'started' };
  }

  /**
   * The refresh token to answer a verified refresh with. Throws `SessionReuseDetected` for a replayed token and
   * `UnauthorizedError` for a family that is gone or ended.
   */
  async rotate(
    presented: string,
    claims: RefreshClaims,
  ): Promise<RotatedSession> {
    return this.judge(presented, claims, true);
  }

  /**
   * A refresh that keeps the presented token (R51-COMPAT, ADR-OPS-07): a body-token client on the compatibility
   * profile that did not ask for rotation, as a release without D9 answered. Nothing is rotated or adopted, but a token
   * its family already replaced, or a family that ended, is judged as `rotate` judges it: a replay ends the family.
   */
  async keep(
    presented: string,
    claims: RefreshClaims,
  ): Promise<RotatedSession> {
    return this.judge(presented, claims, false);
  }

  private async judge(
    presented: string,
    claims: RefreshClaims,
    rotating: boolean,
  ): Promise<RotatedSession> {
    const now = this.clock();
    const repo = this.repo();
    const { userId, orgId } = claims;
    let familyId: string;
    let tokenId: string;
    if (typeof claims.sid === 'string' && typeof claims.jti === 'string') {
      familyId = claims.sid;
      tokenId = claims.jti;
    } else {
      familyId = legacyFamilyId(presented);
      tokenId = LEGACY_TOKEN_ID;
      if (!rotating) {
        // A token from before rotation that no refresh adopted: it works as it always did.
        if ((await repo.get(orgId, familyId)) === null) {
          return { refreshToken: presented, familyId, outcome: 'kept' };
        }
      } else {
        const adopted = await this.adopt(repo, claims, familyId, now);
        if (adopted !== null) return adopted;
      }
    }

    let family = await this.liveFamily(repo, orgId, familyId, userId);
    if (!rotating && family.currentTokenId === tokenId) {
      return { refreshToken: presented, familyId, outcome: 'kept' };
    }
    if (family.currentTokenId === tokenId) {
      const next = newId();
      if (await repo.rotate(orgId, familyId, tokenId, next, now)) {
        return {
          refreshToken: this.mint(
            userId,
            orgId,
            familyId,
            next,
            family.expiresAt,
          ),
          familyId,
          outcome: 'rotated',
        };
      }
      // Another refresh with this token rotated first: judge it as the token just replaced.
      family = await this.liveFamily(repo, orgId, familyId, userId);
    }
    if (
      family.previousTokenId === tokenId &&
      family.rotatedAt !== null &&
      now.getTime() - family.rotatedAt.getTime() <= REFRESH_REUSE_GRACE_MS
    ) {
      if (!rotating) {
        return { refreshToken: presented, familyId, outcome: 'grace' };
      }
      return {
        refreshToken: this.mint(
          userId,
          orgId,
          familyId,
          family.currentTokenId,
          family.expiresAt,
        ),
        familyId,
        outcome: 'grace',
      };
    }
    const endedNow = await repo.revoke(
      orgId,
      familyId,
      REFRESH_REUSE_REASON,
      now,
    );
    throw new SessionReuseDetected(familyId, endedNow);
  }

  /**
   * A sign-out ends the families of the refresh tokens its cookies held (R48MIGFIX-3). The recorded sign-out spares tokens
   * issued in the second before it, so a refresh answered just then would otherwise outlive it. The tokens are read
   * unchecked: only a family of `userId` in `orgId` (the caller the route authenticated) is ended. Returns how many.
   */
  async endSignedOut(
    orgId: string,
    userId: string,
    tokens: readonly string[],
  ): Promise<number> {
    const repo = this.repo();
    let ended = 0;
    for (const token of tokens) {
      const sid = (jwt.decode(token) as { sid?: unknown } | null)?.sid;
      if (typeof sid !== 'string' || !sid) continue;
      const family = await repo.get(orgId, sid);
      if (!family || family.userId !== userId || family.revokedAt !== null)
        continue;
      if (await repo.revoke(orgId, sid, SIGNED_OUT_REASON, this.clock()))
        ended += 1;
    }
    return ended;
  }

  /** A token from before rotation starts its own family; null when an earlier refresh already adopted it. */
  private async adopt(
    repo: SessionFamilyRepository,
    claims: RefreshClaims,
    familyId: string,
    now: Date,
  ): Promise<RotatedSession | null> {
    const { userId, orgId } = claims;
    if (typeof claims.exp !== 'number') {
      throw new UnauthorizedError(SESSION_GONE);
    }
    const expiresAt = new Date(claims.exp * 1000);
    const next = newId();
    const adopted = await repo.start({
      orgId,
      familyId,
      userId,
      currentTokenId: next,
      previousTokenId: LEGACY_TOKEN_ID,
      rotatedAt: now,
      expiresAt,
    });
    if (!adopted) return null;
    return {
      refreshToken: this.mint(userId, orgId, familyId, next, expiresAt),
      familyId,
      outcome: 'adopted',
    };
  }

  private async liveFamily(
    repo: SessionFamilyRepository,
    orgId: string,
    familyId: string,
    userId: string,
  ): Promise<SessionFamily> {
    const family = await repo.get(orgId, familyId);
    if (family === null || family.userId !== userId) {
      throw new UnauthorizedError(SESSION_GONE);
    }
    if (family.revokedAt !== null) {
      throw new UnauthorizedError(SESSION_REPLAYED);
    }
    return family;
  }

  private mint(
    userId: string,
    orgId: string,
    familyId: string,
    tokenId: string,
    expiresAt: Date,
  ): string {
    return refreshTokenJwtGenerator(userId, orgId, this.secret(), {
      sid: familyId,
      jti: tokenId,
      expiresAtSeconds: seconds(expiresAt),
    });
  }
}
