import type {
  NewSessionFamily,
  SessionFamily,
  SessionFamilyRepository,
} from '../../../src/libs/repositories/session-family';

/** The session-family store's contract in memory: the same compare-and-set answers as Mongo and PostgreSQL. */
export class InMemorySessionFamilies implements SessionFamilyRepository {
  readonly rows = new Map<string, SessionFamily>();

  private key(orgId: string, familyId: string): string {
    return `${orgId}\u0000${familyId}`;
  }

  start(family: NewSessionFamily): Promise<boolean> {
    const key = this.key(family.orgId, family.familyId);
    if (this.rows.has(key)) return Promise.resolve(false);
    this.rows.set(key, { ...family, revokedAt: null, revokedReason: null });
    return Promise.resolve(true);
  }

  get(orgId: string, familyId: string): Promise<SessionFamily | null> {
    const row = this.rows.get(this.key(orgId, familyId));
    return Promise.resolve(row ? { ...row } : null);
  }

  rotate(orgId: string, familyId: string, fromTokenId: string, toTokenId: string, at: Date): Promise<boolean> {
    const row = this.rows.get(this.key(orgId, familyId));
    if (!row || row.revokedAt !== null || row.currentTokenId !== fromTokenId) return Promise.resolve(false);
    row.previousTokenId = fromTokenId;
    row.currentTokenId = toTokenId;
    row.rotatedAt = at;
    return Promise.resolve(true);
  }

  revoke(orgId: string, familyId: string, reason: string, at: Date): Promise<boolean> {
    const row = this.rows.get(this.key(orgId, familyId));
    if (!row || row.revokedAt !== null) return Promise.resolve(false);
    row.revokedAt = at;
    row.revokedReason = reason;
    return Promise.resolve(true);
  }
}
