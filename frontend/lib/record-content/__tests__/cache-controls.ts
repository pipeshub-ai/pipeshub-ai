/**
 * Deliberately broken variants of the cache, one per defence. Each removes exactly one behaviour so
 * the reference model can show the invariant that behaviour protects. Test-only: nothing imports
 * this outside `cache.generated.test.ts`.
 */
import {
  RecordContentCache,
  type CacheOptions,
  type Entry,
  type FetchArgs,
  type FetchProgress,
  type Job,
} from '../cache';
import { consumeGrant, type Grant } from '../grant';
import type { CacheFactory, HarnessBreak, ModelBreaks, ServerBreak } from './reference-model';

type Ctor = new (o: CacheOptions) => RecordContentCache;

class NoSingleFlight extends RecordContentCache {
  protected findJob() {
    return undefined; // every open starts its own load
  }
}

class AbortLastWaiter extends RecordContentCache {
  protected partyLeft(job: Job) {
    if (job.waiters.size === 0) job.controller.abort(); // the abort stays in `inflight`
  }
}

class AbortLastWaiterRemove extends RecordContentCache {
  protected partyLeft(job: Job) {
    if (job.waiters.size === 0) this.abortJob(job);
  }
}

class AbandonContinues extends RecordContentCache {
  protected mayRetry(job: Job) {
    return !job.retried; // an abandoned job asks again, outside single flight
  }
}

class EvictOnLeaseOnly extends RecordContentCache {
  protected evictAtInsert() {
    // evicted only when a lease is released
  }
}

class RevokePinned extends RecordContentCache {
  protected drop(entry: Entry) {
    if (this.entries.get(entry.key) === entry) this.entries.delete(entry.key);
    entry.dropped = true;
    this.revoke(entry); // even while a surface shows it
  }
}

class SoftClear extends RecordContentCache {
  protected revokeOnClear(entry: Entry) {
    entry.dropped = true;
    if (entry.pins === 0) this.revoke(entry); // a held lease keeps its URL
  }
}

class NoFence extends RecordContentCache {
  protected isLive(job: Job) {
    // An abandoned job is still dropped, but the answer of a job whose session ended lands.
    return !(job.aborted && job.epoch === this.epoch);
  }
}

class OwnerClearNoFence extends RecordContentCache {
  private ownerChange = false;
  protected moveFence() {
    if (!this.ownerChange) this.epoch += 1;
  }
  protected isLive(job: Job) {
    return job.epoch === this.epoch && job.sessionCurrent(); // no abort flag: the epoch alone is the fence
  }
  acquire(...args: Parameters<RecordContentCache['acquire']>) {
    const before = this.owner;
    const owner = this.ownerFn();
    this.ownerChange = before !== null && owner !== null && before !== owner;
    try {
      return super.acquire(...args);
    } finally {
      this.ownerChange = false;
    }
  }
  clear() {
    super.clear();
    // an owner-change clear leaves late answers of the previous owner alive: jobs keep their epoch
  }
}

class RelabelAny extends RecordContentCache {
  protected acceptsNotModified(current: Entry | undefined): current is Entry {
    return !!current;
  }
}

class NoWindow extends RecordContentCache {
  protected withinWindow() {
    return true;
  }
}

class NoLabelCheck extends RecordContentCache {
  protected sameLabel() {
    return true;
  }
}

class NoInflightBound extends RecordContentCache {
  protected enforceBound() {
    // received bytes of orphaned loads are not bounded
  }
}

class NoAbortUnwanted extends RecordContentCache {
  protected enforceAbort() {
    // an unwanted original keeps downloading
  }
}

class NoDetailsCheck extends RecordContentCache {
  protected checkGrant(grant: unknown, recordId: string, owner: string): Grant | null {
    if (typeof grant === 'string') return { recordId, label: grant, owner, mintedAt: Date.now() };
    return consumeGrant(grant, recordId, owner);
  }
}

class MintFromDetails extends RecordContentCache {
  protected checkGrant(grant: unknown, recordId: string, owner: string): Grant | null {
    const g = grant as { label?: string | null } | null;
    if (g && typeof g === 'object' && !Object.isFrozen(g) && 'label' in g) {
      return { recordId, label: g.label ?? null, owner, mintedAt: Date.now() };
    }
    if (typeof grant === 'string') return null;
    return consumeGrant(grant, recordId, owner);
  }
}

class NoGrantTtl extends RecordContentCache {
  protected checkGrant(grant: unknown, recordId: string, owner: string): Grant | null {
    return consumeGrant(grant, recordId, owner, Number.POSITIVE_INFINITY);
  }
}

class NoEvictOnRefusal extends RecordContentCache {
  evictRecord() {
    // a details refusal leaves the record's entries in place
  }
}

class FetchAfterClear extends RecordContentCache {
  protected mayRetry(job: Job) {
    return !job.retried; // asks again after the session ended
  }
  protected argsFor(job: Job, ifNoneMatch: string | null): FetchArgs {
    // the request ignores the abort, as a transport that does not observe its signal would
    return { ...super.argsFor(job, ifNoneMatch), signal: new AbortController().signal };
  }
}

class CountContentLength extends RecordContentCache {
  protected onProgress(job: Job, p: FetchProgress) {
    const declared = (p as FetchProgress & { contentLength?: number | null }).contentLength;
    super.onProgress(job, { ...p, loaded: declared ?? 0 });
  }
}

export interface Control {
  name: string;
  /** The invariant this control must break (the model's own id). */
  breaks: string;
  cache?: Ctor;
  model?: Partial<ModelBreaks>;
}

const server = (s: ServerBreak): Partial<ModelBreaks> => ({ server: s });
const harness = (h: HarnessBreak): Partial<ModelBreaks> => ({ harness: h });

export const CONTROLS: Control[] = [
  { name: 'no-single-flight', breaks: 'I6', cache: NoSingleFlight },
  { name: 'abort-last-waiter', breaks: 'I9', cache: AbortLastWaiter },
  { name: 'abort-last-waiter-remove', breaks: 'I9', cache: AbortLastWaiterRemove },
  { name: 'abandon-continues', breaks: 'I1b', cache: AbandonContinues },
  { name: 'evict-on-lease-only', breaks: 'I2', cache: EvictOnLeaseOnly },
  { name: 'revoke-pinned', breaks: 'I3', cache: RevokePinned },
  { name: 'soft-clear', breaks: 'I4', cache: SoftClear },
  { name: 'no-fence', breaks: 'I15', cache: NoFence },
  { name: 'owner-clear-no-fence', breaks: 'I15', cache: OwnerClearNoFence },
  { name: 'metadata-tag', breaks: 'I7b', model: server('metadata-tag') },
  { name: 'no-bracket', breaks: 'I7b', model: server('no-bracket') },
  { name: 'no-doc-id', breaks: 'I7b', model: server('no-doc-id') },
  { name: '304-before-access', breaks: 'I8', model: server('304-before-access') },
  { name: 'relabel-any', breaks: 'I10', cache: RelabelAny },
  { name: 'contentrev-in-tag', breaks: 'I11', model: server('contentrev-in-tag') },
  { name: 'no-window', breaks: 'I12', cache: NoWindow },
  { name: 'no-label-check', breaks: 'I12', cache: NoLabelCheck },
  { name: 'no-inflight-bound', breaks: 'I13', cache: NoInflightBound },
  { name: 'no-abort-unwanted', breaks: 'I13', cache: NoAbortUnwanted },
  { name: 'land-own-target', breaks: 'I14', model: harness('land-own-target') },
  { name: 'no-details-check', breaks: 'I8b', cache: NoDetailsCheck },
  { name: 'no-evict-on-refusal', breaks: 'I17', cache: NoEvictOnRefusal },
  { name: 'fetch-after-clear', breaks: 'I1', cache: FetchAfterClear },
  { name: 'count-content-length', breaks: 'I13', cache: CountContentLength },
  { name: 'mint-from-details', breaks: 'I8b', cache: MintFromDetails },
  { name: 'no-grant-ttl', breaks: 'I8c', cache: NoGrantTtl },
];

export function factoryFor(ctor: Ctor | undefined): CacheFactory {
  return (o) => new (ctor ?? RecordContentCache)(o);
}
