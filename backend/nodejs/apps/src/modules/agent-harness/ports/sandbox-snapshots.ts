/**
 * The engine sandbox snapshots stored for each org (OWD-12): one row per
 * stored object, recorded in the transaction of the `engine.snapshotted`
 * event that names it, so the retention sweeper can delete the object once it
 * is older than the org's snapshot TTL.
 */
export interface StoredSandboxSnapshot {
  orgId: string;
  sessionId: string;
  runId: string | null;
  /** The storage document holding the sealed snapshot. */
  objectId: string;
  /** Epoch ms of the event that named it last (DB time). */
  takenAt: number;
  /** The deployment region it was stored in (OWD-12). */
  region: string;
}

export interface SandboxSnapshotIndex<Tx = unknown> {
  /** Records each snapshot; one named again keeps its latest time. */
  record(snapshots: readonly StoredSandboxSnapshot[], tx?: Tx): Promise<void>;
  /** Orgs holding at least one recorded snapshot. */
  orgs(): Promise<string[]>;
  /** The org's snapshots taken before `before` (epoch ms), oldest first. */
  takenBefore(
    orgId: string,
    before: number,
    limit: number,
  ): Promise<StoredSandboxSnapshot[]>;
  /** Drops the rows of deleted objects; a row named again since `before` stays. */
  forget(
    orgId: string,
    objectIds: readonly string[],
    before: number,
  ): Promise<number>;
  /** Drops every row of an erased session (its objects went with its key). */
  forgetSession(orgId: string, sessionId: string): Promise<number>;
}

/** The org's snapshot TTL in days as its policy resolves it (`snapshots.ttlDays`). */
export interface SnapshotTtlSource {
  days(orgId: string): Promise<number>;
}

/** Deletes a stored object for good (every stored copy, then its record). */
export interface StoredObjectPurger {
  purge(orgId: string, objectId: string): Promise<void>;
}
