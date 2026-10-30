/**
 * Credentials: a person's password hash, sign-in code and lockout counters.
 * Callers read the stored hashes (bcrypt runs in the caller) and never write the
 * counters directly; the use cases below do the reservation, release and lock
 * steps as single writes so racing sign-ins cannot both pass.
 *
 * These calls belong to the identity port (`IdentityPort extends CredentialRepository`): a
 * person's secrets are not tied to one organisation, so the same object that answers
 * the pre-sign-in lookups serves them, and only the sign-in code binds it
 * (`getCredentialRepository`). Every call names the account (user id plus organisation
 * id) of the signed-in or signing-in person. In PostgreSQL the work happens
 * as that account: the unit of work runs with its organisation and user.
 */
/**
 * Wrong sign-in attempts (passwords and sign-in codes together) allowed before the person is locked. Both stores take
 * this as the limit when they reserve an attempt.
 */
export const MAX_WRONG_CREDENTIAL_ATTEMPTS = 10;

export interface CredentialDto {
  /** The credential's id (PostgreSQL: the account's id, a person has one). */
  _id: string;
  userId?: string | null;
  orgId: string;
  ipAddress: string;
  otpValidity?: number;
  hashedOTP?: string;
  hashedPassword?: string;
  forceNewPasswordGeneration: boolean;
  wrongCredentialCount: number;
  isBlocked: boolean;
  blockExpiresAt?: Date | null;
  isDeleted: boolean;
  createdAt?: Date;
  updatedAt?: Date;
  __v?: number;
}

export interface CredentialRepository {
  /** The live credential of this person. */
  find(userId: string, orgId: string): Promise<CredentialDto | null>;
  /** Which of these people have a password set (ids of the people with one). */
  userIdsWithPassword(orgId: string, userIds: string[]): Promise<string[]>;
  /** Ends a lock whose time has passed and zeroes the counter. */
  clearBlock(userId: string, orgId: string): Promise<void>;
  /** Locks the credential until `until`. */
  setBlocked(userId: string, orgId: string, until: Date): Promise<void>;
  /**
   * Counts one attempt and returns the credential as counted; null once the
   * credential is locked or has `maxAttempts` already.
   */
  reserveAttempt(
    userId: string,
    orgId: string,
    maxAttempts: number,
  ): Promise<CredentialDto | null>;
  /** Resets the counter to zero only while it still reads `reservedCount` and the credential is not locked. */
  releaseAttempt(
    userId: string,
    orgId: string,
    reservedCount: number,
  ): Promise<void>;
  incrementWrongCount(
    userId: string,
    orgId: string,
  ): Promise<CredentialDto | null>;
  /** Spends the sign-in code: only the request holding this hash gets it. */
  claimOtp(
    userId: string,
    orgId: string,
    hashedOTP: string,
  ): Promise<CredentialDto | null>;
  saveOtp(
    userId: string,
    orgId: string,
    otp: { hashedOTP: string; otpValidity: number; ipAddress: string },
  ): Promise<void>;
  /** Stores the new hash, creating the credential if the person has none. */
  savePassword(
    userId: string,
    orgId: string,
    hashedPassword: string,
    ipAddress?: string,
  ): Promise<void>;
  /** Creates a credential with a starting password. */
  createWithPassword(input: {
    userId: string;
    orgId: string;
    hashedPassword: string;
    ipAddress: string | undefined;
  }): Promise<void>;
  /** Records the sign-in on refresh, creating the credential if missing. */
  touchOnRefresh(
    userId: string,
    orgId: string,
    ipAddress: string | undefined,
  ): Promise<CredentialDto | null>;
  /** Lifts a lock; false when the person was not locked. */
  unblock(userId: string, orgId: string): Promise<boolean>;
  /** Ids of locked people in the organisation, optionally among these. */
  blockedUserIds(orgId: string, among?: string[]): Promise<string[]>;
  /**
   * The account at this address was removed (mark it removed first): its password goes
   * once no live account is left at the address.
   */
  clearPassword(userId: string, email: string): Promise<void>;
  /** The removed account's credential is dropped too (sample accounts). */
  deleteForUser(userId: string, email: string): Promise<void>;
  /** Undoes the one credential written for an account whose creation did not finish (remove the account first). */
  discardForUser(userId: string, email: string): Promise<void>;
}
