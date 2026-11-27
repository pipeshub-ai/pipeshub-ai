import type {
  SandboxSnapshotIndex,
  StoredSandboxSnapshot,
} from '../ports/sandbox-snapshots';

export class InMemorySandboxSnapshotIndex implements SandboxSnapshotIndex {
  readonly rows = new Map<string, StoredSandboxSnapshot>();

  record(snapshots: readonly StoredSandboxSnapshot[]): Promise<void> {
    for (const snapshot of snapshots) {
      const key = `${snapshot.orgId}:${snapshot.objectId}`;
      const held = this.rows.get(key);
      this.rows.set(
        key,
        held === undefined
          ? { ...snapshot }
          : { ...held, takenAt: Math.max(held.takenAt, snapshot.takenAt) },
      );
    }
    return Promise.resolve();
  }

  orgs(): Promise<string[]> {
    return Promise.resolve([
      ...new Set([...this.rows.values()].map((row) => row.orgId)),
    ]);
  }

  takenBefore(
    orgId: string,
    before: number,
    limit: number,
  ): Promise<StoredSandboxSnapshot[]> {
    return Promise.resolve(
      [...this.rows.values()]
        .filter((row) => row.orgId === orgId && row.takenAt < before)
        .sort((a, b) => a.takenAt - b.takenAt)
        .slice(0, limit)
        .map((row) => ({ ...row })),
    );
  }

  forget(
    orgId: string,
    objectIds: readonly string[],
    before: number,
  ): Promise<number> {
    let forgotten = 0;
    for (const objectId of objectIds) {
      const key = `${orgId}:${objectId}`;
      const held = this.rows.get(key);
      if (held !== undefined && held.takenAt < before) {
        this.rows.delete(key);
        forgotten += 1;
      }
    }
    return Promise.resolve(forgotten);
  }

  forgetSession(orgId: string, sessionId: string): Promise<number> {
    let forgotten = 0;
    for (const [key, row] of this.rows) {
      if (row.orgId === orgId && row.sessionId === sessionId) {
        this.rows.delete(key);
        forgotten += 1;
      }
    }
    return Promise.resolve(forgotten);
  }
}
