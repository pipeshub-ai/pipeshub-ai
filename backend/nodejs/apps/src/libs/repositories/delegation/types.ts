/**
 * Delegation repository: the allow-list that lets a service account act as one
 * named person, and the log of every exchange attempt. Rows leave as plain
 * objects with string ids.
 */
export type DelegationOutcome = 'granted' | 'revoked' | 'issued' | 'refused';

export interface DelegationRowDto {
  _id: string;
  orgId: string;
  serviceAccountId: string;
  subjectUserId: string;
  grantedBy: string;
  reason?: string;
  createdAt: Date;
  updatedAt: Date;
}

export interface DelegationEventDto {
  _id: string;
  orgId: string;
  serviceAccountId: string;
  subjectUserId?: string;
  requestedSubject: string;
  outcome: DelegationOutcome;
  refusedReason?: string;
  actedBy?: string;
  note?: string;
  scopes: string[];
  accessTokenId?: string;
  jti?: string;
  expiresAt?: Date;
  createdAt: Date;
}

export interface NewDelegationEvent {
  orgId: string;
  serviceAccountId: string;
  subjectUserId?: string;
  requestedSubject: string;
  outcome: DelegationOutcome;
  refusedReason?: string;
  actedBy?: string;
  note?: string;
  scopes?: string[];
  accessTokenId?: string;
  jti?: string;
  expiresAt?: Date;
}

export interface DelegationRepository {
  isAllowed(
    orgId: string,
    serviceAccountId: string,
    subjectUserId: string,
  ): Promise<boolean>;
  countFor(orgId: string, serviceAccountId: string): Promise<number>;
  /**
   * Adds the entry. When it already exists the stored row comes back with
   * `created: false`; any other store error propagates.
   */
  grant(input: {
    orgId: string;
    serviceAccountId: string;
    subjectUserId: string;
    grantedBy: string;
    reason?: string;
  }): Promise<{ row: DelegationRowDto; created: boolean }>;
  /** Newest first. */
  listFor(orgId: string, serviceAccountId: string): Promise<DelegationRowDto[]>;
  /** True when an entry was removed. */
  revoke(
    orgId: string,
    serviceAccountId: string,
    subjectUserId: string,
  ): Promise<boolean>;
  /** Number of entries removed. */
  revokeAllFor(orgId: string, serviceAccountId: string): Promise<number>;
  recordEvent(event: NewDelegationEvent): Promise<void>;
  /** Newest first; one service account or the whole organisation. */
  listEvents(
    orgId: string,
    serviceAccountId: string | undefined,
    limit: number,
  ): Promise<DelegationEventDto[]>;
}
