/**
 * Session families (ADR-WEB-02 D9, R43AUTH-FU-1): one per sign-in on one device. Every refresh replaces the family's
 * refresh token with a new one; the family remembers which token is current and the one just replaced, so a token
 * presented after it was replaced is seen as a copy and ends the family.
 *
 * Only token ids are kept (the `jti` of each refresh token), never a token: a copy of the store opens nothing.
 */
export interface SessionFamily {
  orgId: string;
  familyId: string;
  userId: string;
  /** The id of the one refresh token that may rotate the family now. */
  currentTokenId: string;
  /** The id of the token the last rotation replaced; null before the first rotation. */
  previousTokenId: string | null;
  /** When `previousTokenId` was replaced (the start of its grace period); null before the first rotation. */
  rotatedAt: Date | null;
  /** The end of the sign-in: every token of the family expires then, a rotation does not extend it. */
  expiresAt: Date;
  revokedAt: Date | null;
  revokedReason: string | null;
}

export type NewSessionFamily = Omit<
  SessionFamily,
  'revokedAt' | 'revokedReason'
>;

export interface SessionFamilyRepository {
  /** Creates the family; false when one with this id already exists (nothing is changed then). */
  start(family: NewSessionFamily): Promise<boolean>;
  get(orgId: string, familyId: string): Promise<SessionFamily | null>;
  /**
   * Moves a live family from `fromTokenId` to `toTokenId` as one compare-and-set: of two refreshes presenting the same
   * token only one rotates. False when the family is revoked or no longer at `fromTokenId`.
   */
  rotate(
    orgId: string,
    familyId: string,
    fromTokenId: string,
    toTokenId: string,
    at: Date,
  ): Promise<boolean>;
  /** Ends the family; true only for the call that ended it. */
  revoke(
    orgId: string,
    familyId: string,
    reason: string,
    at: Date,
  ): Promise<boolean>;
}
