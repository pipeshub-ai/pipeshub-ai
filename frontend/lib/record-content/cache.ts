/**
 * In-memory cache of record bytes for the file preview.
 *
 * One instance per tab, in module state. Nothing is written to any browser storage. Every entry
 * belongs to one `(org, user)` taken from the access token, and nothing survives the session.
 *
 * - Leases: a surface that shows a document holds a lease on its entry. The lease owns the object
 *   URL; the URL is revoked when the entry is dropped and nobody holds it, and at session end for
 *   every entry whatever leases are held.
 * - Single flight: concurrent opens of one key share one load. A party that leaves does not abort
 *   it; the job runs on so a re-open joins it instead of fetching again.
 * - Retention: after the last release an entry stays only when it can be revalidated (a storage
 *   `ETag`) or is reused by label inside a window (connector records, with a single-use grant from
 *   this open's details call). Everything else is lease-only.
 * - Budget: unpinned bytes are bounded and evicted least recently used at insert. Bytes of loads
 *   nobody waits for are bounded too, and an unwanted original is aborted as soon as its headers
 *   show it will not be kept.
 */

import { clearSessionCache, captureSession, onSessionEnd } from '@/lib/api/session-cache';
import { getOrgIdFromToken, getUserIdFromToken } from '@/lib/utils/jwt';
import { consumeGrant, type Grant } from './grant';

export type Variant = 'original' | 'pdf';

export const RECORD_CONTENT_CACHE_MAX_ENTRY_BYTES = 64 * 1024 * 1024;
export const RECORD_CONTENT_CACHE_MAX_ENTRIES = 24;
export const RECORD_CONTENT_CONNECTOR_FRESH_MS = 600_000;

const MIB = 1024 * 1024;

/** 96 MiB on devices that report 4 GB of memory or less, 192 MiB otherwise. */
export function defaultBudgetBytes(): number {
  const mem =
    typeof navigator !== 'undefined'
      ? (navigator as Navigator & { deviceMemory?: number }).deviceMemory
      : undefined;
  return typeof mem === 'number' && mem <= 4 ? 96 * MIB : 192 * MIB;
}

export interface Lease {
  /** Created on first read. Empty once the session ended. */
  readonly url: string;
  readonly blob: Blob;
  readonly mime: string;
  readonly etag: string | null;
  /** Idempotent. After the session ended it does nothing. */
  release(): void;
}

export interface FetchProgress {
  /** Bytes received so far (never `Content-Length`: connector and conversion answers send none). */
  loaded: number;
  status: number;
  etag: string | null;
}

export interface FetchArgs {
  recordId: string;
  variant: Variant;
  ifNoneMatch: string | null;
  signal: AbortSignal;
  onProgress: (p: FetchProgress) => void;
  /** Passed to apiClient as is: the caller renders the failure itself. */
  suppressErrorToast?: boolean;
  /** Retention is on: ask the server for a validator with the bytes. Off, the request is the plain stream request. */
  wantsValidator?: boolean;
  /** The connector grant label this load was made for. Loads of different labels run apart. Informational for the transport. */
  label?: string | null;
}

export type FetchResult =
  | { status: 200; blob: Blob; etag: string | null; mime: string }
  | { status: 304 };

export type RecordContentFetcher = (args: FetchArgs) => Promise<FetchResult>;

export interface Pending {
  promise: Promise<Lease>;
  /** The surface no longer wants the answer. The load itself keeps running. */
  leave(): void;
}

export class SessionEndedError extends Error {
  constructor() {
    super('Session ended');
    this.name = 'SessionEndedError';
  }
}

export class PartyLeftError extends Error {
  constructor() {
    super('Party left');
    this.name = 'PartyLeftError';
  }
}

export interface Entry {
  key: string;
  recordId: string;
  variant: Variant;
  etag: string | null;
  label: string | null;
  fetchedAt: number;
  blob: Blob;
  bytes: number;
  mime: string;
  url: string | null;
  pins: number;
  lastUsed: number;
  /** Removed from the map; kept alive only for the leases still held. */
  dropped: boolean;
  epoch: number;
}

interface Waiter {
  resolve: (lease: Lease) => void;
  reject: (error: unknown) => void;
}

export interface Job {
  key: string;
  recordId: string;
  variant: Variant;
  label: string | null;
  /** Start order among the jobs of this cache; the newest job of a key owns its retained entry. */
  seq: number;
  waiters: Set<Waiter>;
  controller: AbortController;
  received: number;
  etagSeen: string | null;
  sawHeaders: boolean;
  settled: boolean;
  aborted: boolean;
  startedAt: number;
  epoch: number;
  sessionCurrent: () => boolean;
  /** An answer to a conditional request that would need a second, unconditional request. */
  retried: boolean;
}

export interface CacheOptions {
  fetcher: RecordContentFetcher;
  /** `(org, user)` of the signed-in session, or null. */
  owner?: () => string | null;
  budgetBytes?: () => number;
  /** Largest entry kept after its last release. Defaults to 64 MiB. */
  maxEntryBytes?: () => number;
  /** Retention switch. Off: no entry is kept and no validator is sent; sharing and leases still work. */
  enabled?: () => boolean;
  now?: () => number;
  /** Called when a different token owner shows up between two opens. Defaults to ending the session. */
  onOwnerChange?: () => void;
  createObjectURL?: (blob: Blob) => string;
  revokeObjectURL?: (url: string) => void;
}

function tokenOwner(): string | null {
  const org = getOrgIdFromToken();
  const user = getUserIdFromToken();
  return org && user ? `${org}|${user}` : null;
}

export class RecordContentCache {
  protected readonly fetcher: RecordContentFetcher;
  protected readonly ownerFn: () => string | null;
  protected readonly budgetFn: () => number;
  protected readonly maxEntryFn: () => number;
  protected readonly enabledFn: () => boolean;
  protected readonly now: () => number;
  protected readonly onOwnerChange: () => void;
  protected readonly mkUrl: (blob: Blob) => string;
  protected readonly rmUrl: (url: string) => void;

  protected owner: string | null = null;
  protected epoch = 0;
  protected tick = 0;
  protected readonly entries = new Map<string, Entry>();
  /** Every entry that still holds an object URL, in the map or dropped but leased. */
  protected readonly live = new Set<Entry>();
  protected readonly inflight = new Map<string, Job>();
  /** Every unsettled job, in `inflight` or not (superseded, abandoned, orphaned). */
  protected readonly jobs = new Set<Job>();
  /** The `seq` of the newest job started for each key: only that job may retain its result. */
  protected readonly newestSeq = new Map<string, number>();
  protected jobSeq = 0;

  constructor(options: CacheOptions) {
    this.fetcher = options.fetcher;
    this.ownerFn = options.owner ?? tokenOwner;
    this.budgetFn = options.budgetBytes ?? defaultBudgetBytes;
    this.maxEntryFn = options.maxEntryBytes ?? (() => RECORD_CONTENT_CACHE_MAX_ENTRY_BYTES);
    this.enabledFn = options.enabled ?? (() => true);
    this.now = options.now ?? Date.now;
    this.onOwnerChange = options.onOwnerChange ?? (() => clearSessionCache());
    this.mkUrl = options.createObjectURL ?? ((b) => URL.createObjectURL(b));
    this.rmUrl = options.revokeObjectURL ?? ((u) => URL.revokeObjectURL(u));
  }

  // ── Introspection (tests and the reporting hooks) ──────────────────────

  stats() {
    let unpinnedBytes = 0;
    for (const e of this.entries.values()) if (e.pins === 0) unpinnedBytes += e.bytes;
    let orphanedBytes = 0;
    for (const j of this.jobs) if (this.isOrphan(j) && !j.aborted) orphanedBytes += j.received;
    return {
      entries: this.entries.size,
      unpinnedBytes,
      orphanedBytes,
      inflight: this.inflight.size,
      liveUrls: [...this.live].filter((e) => e.url !== null).length,
    };
  }

  /** The fence: moves on every clear (session end, owner change). */
  fenceEpoch(): number {
    return this.epoch;
  }

  /** Read-only view of the retained entries and running loads, for tests and reporting. */
  snapshot() {
    return {
      owner: this.owner,
      epoch: this.epoch,
      entries: [...this.entries.values()].map((e) => ({
        key: e.key,
        recordId: e.recordId,
        variant: e.variant,
        etag: e.etag,
        label: e.label,
        fetchedAt: e.fetchedAt,
        bytes: e.bytes,
        pins: e.pins,
        epoch: e.epoch,
      })),
      jobs: [...this.jobs].map((j) => ({
        key: j.key,
        variant: j.variant,
        waiters: j.waiters.size,
        received: j.received,
        aborted: j.aborted,
        sawHeaders: j.sawHeaders,
        unkept: j.sawHeaders && j.etagSeen === null && j.label === null,
        epoch: j.epoch,
      })),
    };
  }

  has(recordId: string, variant: Variant): boolean {
    const owner = this.ownerFn();
    return owner !== null && this.entries.has(this.keyOf(owner, recordId, variant));
  }

  // ── Opening ────────────────────────────────────────────────────────────

  /**
   * Opens a record's content. `grant` is the single-use grant from this open's details call; only it
   * can make a connector copy reusable without a request.
   */
  acquire(recordId: string, variant: Variant, grant?: Grant): Pending {
    let waiter!: Waiter;
    const promise = new Promise<Lease>((resolve, reject) => {
      waiter = { resolve, reject };
    });
    let job: Job | null = null;
    let left = false;
    const leave = () => {
      if (left) return;
      left = true;
      if (job) {
        job.waiters.delete(waiter);
        this.partyLeft(job);
      }
      waiter.reject(new PartyLeftError());
    };
    // A rejected, unobserved pending must not surface as an unhandled rejection.
    promise.catch(() => undefined);

    const owner = this.ownerFn();
    if (owner === null) {
      waiter.reject(new SessionEndedError());
      return { promise, leave };
    }
    if (this.owner !== null && this.owner !== owner) {
      this.onOwnerChange();
      // The default hook ends the session, which clears this cache. A custom one may not.
      if (this.owner !== null && this.owner !== owner) this.clear();
    }
    this.owner = owner;

    const key = this.keyOf(owner, recordId, variant);
    const checked = this.checkGrant(grant, recordId, owner);

    const entry = this.entries.get(key);
    if (entry && this.enabledFn() && this.isLabelHit(entry, checked)) {
      waiter.resolve(this.lease(entry));
      return { promise, leave };
    }

    job = this.findJob(key, checked?.label ?? null) ?? this.startJob(key, recordId, variant, checked?.label ?? null, entry ?? null);
    job.waiters.add(waiter);
    return { promise, leave };
  }

  protected isLabelHit(entry: Entry, grant: Grant | null): boolean {
    return (
      grant !== null &&
      grant.label !== null &&
      entry.label !== null &&
      this.sameLabel(entry, grant) &&
      this.withinWindow(entry)
    );
  }

  protected sameLabel(entry: Entry, grant: Grant): boolean {
    return grant.label === entry.label;
  }

  protected withinWindow(entry: Entry): boolean {
    return this.now() - entry.fetchedAt <= RECORD_CONTENT_CONNECTOR_FRESH_MS;
  }

  /**
   * The load of a key that is already running, if any (single flight). Only a load made with the same
   * connector label is shared: an open that holds a different grant asked for a different result.
   */
  protected findJob(key: string, label: string | null): Job | undefined {
    let found: Job | undefined;
    for (const job of this.jobs) {
      if (job.key === key && job.label === label && (!found || job.seq > found.seq)) found = job;
    }
    return found;
  }

  /** The grant of this open if it is genuine; spends it. */
  protected checkGrant(grant: unknown, recordId: string, owner: string): Grant | null {
    return consumeGrant(grant, recordId, owner);
  }

  /** Whether a 304 may re-label `current`: only the entry the validator was sent for. */
  protected acceptsNotModified(current: Entry | undefined, sent: string | null): current is Entry {
    return !!current && sent !== null && current.etag === sent && !current.dropped;
  }

  /** Whether a job may ask again in full after a 304 that matched no entry. */
  protected mayRetry(job: Job): boolean {
    return this.isLive(job) && job.waiters.size > 0 && !job.retried;
  }

  /** The last party of a load left: the load runs on, and only the bounds are checked. */
  protected partyLeft(job: Job): void {
    this.enforceAbort(job);
    this.enforceBound();
  }

  /** Makes room after an entry was inserted. */
  protected evictAtInsert(): void {
    this.evict();
  }

  /** What a hard clear does to an entry that still holds an object URL. */
  protected revokeOnClear(entry: Entry): void {
    entry.dropped = true;
    this.revoke(entry);
  }

  /** A clear (session end or owner change) moves the fence so a late answer is discarded. */
  protected moveFence(): void {
    this.epoch += 1;
  }

  protected keyOf(owner: string, recordId: string, variant: Variant): string {
    return `${owner}|${recordId}|${variant}`;
  }

  // ── Loading ────────────────────────────────────────────────────────────

  protected startJob(
    key: string,
    recordId: string,
    variant: Variant,
    label: string | null,
    entry: Entry | null,
  ): Job {
    const job: Job = {
      key,
      recordId,
      variant,
      label,
      seq: ++this.jobSeq,
      waiters: new Set(),
      controller: new AbortController(),
      received: 0,
      etagSeen: null,
      sawHeaders: false,
      settled: false,
      aborted: false,
      startedAt: this.now(),
      epoch: this.epoch,
      sessionCurrent: captureSession(),
      retried: false,
    };
    this.inflight.set(key, job);
    this.newestSeq.set(key, job.seq);
    this.jobs.add(job);
    void this.run(job, entry);
    return job;
  }

  protected async run(job: Job, entry: Entry | null): Promise<void> {
    // A validator is only worth sending for an entry that holds one, and never when retention is off.
    let sent = entry && entry.etag && this.enabledFn() ? entry.etag : null;
    try {
      let result = await this.fetcher(this.argsFor(job, sent));
      if (result.status === 304) {
        // Reuse only what this validator was sent for; anything else is asked again in full, once.
        const current = this.entries.get(job.key);
        if (this.acceptsNotModified(current, sent)) {
          this.land(job, current, null);
          return;
        }
        if (!this.mayRetry(job)) {
          this.finish(job);
          return;
        }
        job.retried = true;
        sent = null;
        result = await this.fetcher(this.argsFor(job, null));
        if (result.status === 304) throw new Error('Unexpected 304 without a validator');
      }
      if (!this.isLive(job)) {
        this.finish(job);
        return;
      }
      this.land(job, null, result);
    } catch (error) {
      if (!this.isLive(job)) {
        this.finish(job);
        return;
      }
      const waiters = [...job.waiters];
      this.finish(job);
      // A refused open leaves nothing of the record behind: what was retained was served to a caller
      // who could have had it, and no longer can.
      const status = (error as { statusCode?: number } | null)?.statusCode;
      if (status === 403 || status === 404 || status === 409) this.evictRecord(job.recordId);
      for (const w of waiters) w.reject(error);
    }
  }

  protected argsFor(job: Job, ifNoneMatch: string | null): FetchArgs {
    return {
      recordId: job.recordId,
      variant: job.variant,
      ifNoneMatch,
      signal: job.controller.signal,
      onProgress: (p) => this.onProgress(job, p),
      wantsValidator: this.enabledFn(),
      label: job.label,
    };
  }

  protected isLive(job: Job): boolean {
    return !job.aborted && job.epoch === this.epoch && job.sessionCurrent();
  }

  protected finish(job: Job): void {
    job.settled = true;
    this.jobs.delete(job);
    if (this.inflight.get(job.key) === job) this.inflight.delete(job.key);
    // The mark outlives the newest job while an older one of the key still runs, so that one cannot replace its result.
    if (![...this.jobs].some((j) => j.key === job.key)) this.newestSeq.delete(job.key);
  }

  protected onProgress(job: Job, p: FetchProgress): void {
    if (job.settled || job.aborted) return;
    job.received = p.loaded;
    if (!job.sawHeaders) {
      job.sawHeaders = true;
      job.etagSeen = p.etag;
    }
    this.enforceAbort(job);
    this.enforceBound();
  }

  /** No waiter, and it will not be retained (no validator, no label) or it is too big to keep. */
  protected enforceAbort(job: Job): void {
    if (job.settled || job.aborted || job.waiters.size > 0 || job.variant !== 'original') return;
    const tooBig = job.received > this.maxEntryFn();
    const unkept = job.sawHeaders && job.etagSeen === null && job.label === null;
    if (tooBig || unkept) this.abortJob(job);
  }

  protected isOrphan(job: Job): boolean {
    return !job.settled && job.waiters.size === 0;
  }

  /** Received bytes of loads nobody waits for stay within the budget: oldest originals first. */
  protected enforceBound(): void {
    const budget = this.budgetFn();
    const total = () => {
      let sum = 0;
      for (const j of this.jobs) if (this.isOrphan(j) && !j.aborted) sum += j.received;
      return sum;
    };
    if (total() <= budget) return;
    const orphans = [...this.jobs]
      .filter((j) => this.isOrphan(j) && !j.aborted)
      .sort((a, b) => {
        if (a.variant !== b.variant) return a.variant === 'original' ? -1 : 1;
        return a.startedAt - b.startedAt;
      });
    for (const j of orphans) {
      if (total() <= budget) break;
      this.abortJob(j);
    }
  }

  protected abortJob(job: Job): void {
    job.aborted = true;
    job.controller.abort();
    this.finish(job);
  }

  // ── Landing ────────────────────────────────────────────────────────────

  protected land(job: Job, reuse: Entry | null, result: Extract<FetchResult, { status: 200 }> | null): void {
    const waiters = [...job.waiters];
    const newest = (this.newestSeq.get(job.key) ?? job.seq) <= job.seq;
    this.finish(job);
    let entry: Entry | null = reuse;
    if (!entry && result) {
      const candidate: Entry = {
        key: job.key,
        recordId: job.recordId,
        variant: job.variant,
        etag: result.etag,
        label: job.label,
        fetchedAt: job.startedAt,
        blob: result.blob,
        bytes: result.blob.size,
        mime: result.mime,
        url: null,
        pins: 0,
        lastUsed: ++this.tick,
        dropped: true,
        epoch: this.epoch,
      };
      const keep =
        this.enabledFn() &&
        // A superseded job answers its own waiters but does not replace what a newer open retained.
        newest &&
        candidate.bytes <= this.maxEntryFn() &&
        (candidate.etag !== null || candidate.label !== null);
      if (keep) {
        const old = this.entries.get(job.key);
        if (old) this.drop(old);
        candidate.dropped = false;
        this.entries.set(job.key, candidate);
        this.evictAtInsert();
      }
      entry = candidate;
    }
    if (!entry) return;
    for (const w of waiters) w.resolve(this.lease(entry));
    // Nobody waited: a kept entry stays unpinned; anything else was never given a URL.
  }

  // ── Leases, entries ────────────────────────────────────────────────────

  protected lease(entry: Entry): Lease {
    entry.pins += 1;
    entry.lastUsed = ++this.tick;
    let released = false;
    const urlOf = () => this.urlOf(entry);
    const unpin = () => this.unpin(entry);
    return {
      // Made on first use: a renderer that takes the Blob (DOCX) never needs an object URL.
      get url() {
        return urlOf();
      },
      blob: entry.blob,
      mime: entry.mime,
      etag: entry.etag,
      release() {
        if (released) return;
        released = true;
        unpin();
      },
    };
  }

  protected urlOf(entry: Entry): string {
    if (entry.url === null) {
      // A lease from an ended session or a dropped entry has no URL to hand out.
      if (entry.epoch !== this.epoch || (entry.dropped && entry.pins === 0)) return '';
      entry.url = this.mkUrl(entry.blob);
      this.live.add(entry);
    }
    return entry.url;
  }

  protected unpin(entry: Entry): void {
    if (entry.pins > 0) entry.pins -= 1;
    if (entry.pins === 0) {
      if (entry.dropped) this.revoke(entry);
      else this.evict();
    }
  }

  protected revoke(entry: Entry): void {
    if (entry.url !== null) {
      try {
        this.rmUrl(entry.url);
      } catch {
        // A URL that cannot be revoked is gone with the page.
      }
      entry.url = null;
    }
    this.live.delete(entry);
  }

  protected drop(entry: Entry): void {
    if (this.entries.get(entry.key) === entry) this.entries.delete(entry.key);
    entry.dropped = true;
    if (entry.pins === 0) this.revoke(entry);
  }

  /** Least recently used unpinned entries go until the budget and the entry cap hold. */
  protected evict(): void {
    const budget = this.budgetFn();
    const unpinned = () => [...this.entries.values()].filter((e) => e.pins === 0);
    const bytes = () => unpinned().reduce((n, e) => n + e.bytes, 0);
    while (bytes() > budget || this.entries.size > RECORD_CONTENT_CACHE_MAX_ENTRIES) {
      const victims = unpinned().sort((a, b) => a.lastUsed - b.lastUsed);
      if (victims.length === 0) break;
      this.drop(victims[0]);
    }
  }

  /** Every variant of a record for the current owner, after a refusal. */
  evictRecord(recordId: string): void {
    for (const e of [...this.entries.values()]) {
      if (e.recordId === recordId && e.key.startsWith(`${this.owner ?? ''}|`)) this.drop(e);
    }
  }

  // ── Session end ────────────────────────────────────────────────────────

  /**
   * Hard clear: aborts every load, drops every entry and revokes every object URL this cache
   * created, leased or not. Waiters are released here because `apiClient` never settles the
   * answer of an ended session. The epoch moves so a late answer is discarded.
   */
  clear(): void {
    this.moveFence();
    this.owner = null;
    const jobs = [...this.jobs];
    this.jobs.clear();
    this.inflight.clear();
    this.newestSeq.clear();
    for (const job of jobs) {
      job.aborted = true;
      job.settled = true;
      try {
        job.controller.abort();
      } catch {
        // already aborted
      }
      const waiters = [...job.waiters];
      job.waiters.clear();
      for (const w of waiters) w.reject(new SessionEndedError());
    }
    for (const entry of [...this.live]) this.revokeOnClear(entry);
    for (const entry of this.entries.values()) entry.dropped = true;
    this.entries.clear();
  }
}

// ── The tab's instance ───────────────────────────────────────────────────

let instance: RecordContentCache | null = null;
let unsubscribe: (() => void) | null = null;

/** Wires the tab's cache with its fetcher. Called once by `index.ts`. */
export function createRecordContentCache(options: CacheOptions): RecordContentCache {
  unsubscribe?.();
  instance?.clear();
  const cache = new RecordContentCache(options);
  instance = cache;
  unsubscribe = onSessionEnd(() => cache.clear());
  return cache;
}

export function getRecordContentCache(): RecordContentCache {
  if (!instance) throw new Error('Record content cache is not initialised');
  return instance;
}
