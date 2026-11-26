import type { ChangeEvent } from '../contracts/generated';
import { ControlPlaneUnavailableError } from '../domain/errors';
import type { Snapshot } from '../domain/resolution';
import { changeStep, touchKey } from '../domain/revisions';
import type { HeadRevisions, Heads } from '../ports/head-revisions';
import type { SnapshotSource } from '../ports/snapshot-source';

/** `heads`: the org's as the cache read them just before; absent, the loader reads them. */
export type SnapshotLoader = (
  orgId: string,
  agentKey: string | null,
  actorId: string,
  heads?: Heads,
) => Promise<Snapshot>;

export interface SnapshotCacheOptions {
  load: SnapshotLoader;
  /** Asked before an entry is served; absent, the cache is kept current by `onChange` alone. */
  heads?: HeadRevisions;
  /** Entries held (LRU); 02-lld §8.1's principal tier by default. */
  bound?: number;
  /** How long an entry may be served while the head cannot be read (NFR-31, PIN-21). */
  staleMs?: number;
  /**
   * The oldest a loaded entry is served, head unchanged or not: what the
   * snapshot reads outside the head (team membership, CPD-11) reaches pins
   * within it. Absent: no limit.
   */
  maxAgeMs?: number;
  now?: () => number;
}

interface Entry {
  snapshot: Snapshot;
  /** When the head last confirmed it (or it was loaded). */
  confirmedAt: number;
  loadedAt: number;
}

/**
 * `ResolvedSnapshotCache` (03-interfaces §4.5, 02-lld §8.1): what a pin reads,
 * per (org, agent, actor), kept current by the revision rule. An event right
 * after an entry advances it unless it touched what the entry depends on; a
 * gap drops the org. Before serving, the org head is read: an entry behind
 * it is a write whose event this instance missed, so the org is dropped and
 * reloaded; an entry behind the deployment's revision (a write to a document
 * every org shares) reloads alone. With the head unreadable an entry confirmed in the last
 * `staleMs` is served; anything older fails closed (`CP_UNAVAILABLE`).
 * Keys carry the org, so two orgs never share an entry.
 */
export class ResolvedSnapshotCache implements SnapshotSource {
  private readonly entries = new Map<string, Entry>();
  private readonly reading = new Map<string, Promise<Snapshot>>();
  private readonly bound: number;
  private readonly staleMs: number;
  private readonly now: () => number;

  constructor(private readonly options: SnapshotCacheOptions) {
    this.bound = options.bound ?? 50_000;
    this.staleMs = options.staleMs ?? 30_000;
    this.now = options.now ?? Date.now;
  }

  /**
   * One read for callers that ask at the same moment (an admission's routing,
   * its agent's effective-set review and its pin): they share the head check
   * and, on a miss, the load. Nothing is kept once it settles, so a later
   * request reads the head again.
   */
  snapshot(
    orgId: string,
    agentKey: string | null,
    actorId: string,
  ): Promise<Snapshot> {
    const id = JSON.stringify([orgId, agentKey, actorId]);
    const pending = this.reading.get(id);
    if (pending !== undefined) return pending;
    const read: Promise<Snapshot> = this.read(
      id,
      orgId,
      agentKey,
      actorId,
    ).finally(() => {
      if (this.reading.get(id) === read) this.reading.delete(id);
    });
    this.reading.set(id, read);
    return read;
  }

  private async read(
    id: string,
    orgId: string,
    agentKey: string | null,
    actorId: string,
  ): Promise<Snapshot> {
    const cached = this.entries.get(id);
    const maxAge = this.options.maxAgeMs;
    const entry =
      cached !== undefined &&
      maxAge !== undefined &&
      this.now() - cached.loadedAt > maxAge
        ? undefined
        : cached;
    const head = await this.head(orgId, entry);
    if (entry !== undefined) {
      if (head === 'unchecked' || head === 'stale-ok')
        return this.hit(id, entry);
      if (
        head.org === entry.snapshot.headRevision &&
        head.deployment === entry.snapshot.deploymentRevision
      ) {
        entry.confirmedAt = this.now();
        return this.hit(id, entry);
      }
      if (head.org > entry.snapshot.headRevision) this.dropOrg(orgId);
    }
    // The head just read is the one a load reads first (PERF-E2: one read of it, not two).
    const snapshot = await this.options.load(
      orgId,
      agentKey,
      actorId,
      typeof head === 'object' ? head : undefined,
    );
    this.entries.delete(id);
    this.entries.set(id, {
      snapshot,
      confirmedAt: this.now(),
      loadedAt: this.now(),
    });
    while (this.entries.size > this.bound) {
      this.entries.delete(this.entries.keys().next().value as string);
    }
    return snapshot;
  }

  onChange(event: ChangeEvent): void {
    this.unshare(event.orgId);
    const touched = event.touched.map((ref) => touchKey(ref.kind, ref.id));
    for (const [id, entry] of [...this.entries]) {
      if (entry.snapshot.orgId !== event.orgId) continue;
      const step = changeStep(entry.snapshot.headRevision, event);
      if (step === 'duplicate') continue;
      if (
        step === 'gap' ||
        touched.some((key) => entry.snapshot.dependsOn.has(key))
      ) {
        this.entries.delete(id);
        continue;
      }
      entry.snapshot = { ...entry.snapshot, headRevision: event.revision };
    }
  }

  /**
   * A committed write of the org, whatever it touched: every entry loaded
   * before it reloads on its next read, and an event already seen drops
   * nothing (delivery is at least once).
   */
  dropBehind(event: Pick<ChangeEvent, 'orgId' | 'revision'>): void {
    this.unshare(event.orgId);
    for (const [id, entry] of [...this.entries]) {
      if (
        entry.snapshot.orgId === event.orgId &&
        entry.snapshot.headRevision < event.revision
      ) {
        this.entries.delete(id);
      }
    }
  }

  /** Drops every entry of the org: the next pin reloads it (admission's `CP_STALE` re-pin). */
  refresh(orgId: string): void {
    this.unshare(orgId);
    this.dropOrg(orgId);
  }

  /** A read begun before a write of the org may hold what the write replaced: a later caller starts its own. */
  private unshare(orgId: string): void {
    for (const id of [...this.reading.keys()]) {
      if ((JSON.parse(id) as [string])[0] === orgId) this.reading.delete(id);
    }
  }

  get size(): number {
    return this.entries.size;
  }

  private async head(
    orgId: string,
    entry: Entry | undefined,
  ): Promise<Heads | 'unchecked' | 'stale-ok'> {
    if (this.options.heads === undefined) return 'unchecked';
    try {
      return await this.options.heads.heads(orgId);
    } catch (error) {
      if (
        entry !== undefined &&
        this.now() - entry.confirmedAt < this.staleMs
      ) {
        return 'stale-ok';
      }
      throw new ControlPlaneUnavailableError(
        'The control plane head revision cannot be read',
        { cause: error },
      );
    }
  }

  private hit(id: string, entry: Entry): Snapshot {
    this.entries.delete(id);
    this.entries.set(id, entry);
    return entry.snapshot;
  }

  private dropOrg(orgId: string): void {
    for (const [id, entry] of [...this.entries]) {
      if (entry.snapshot.orgId === orgId) this.entries.delete(id);
    }
  }
}
