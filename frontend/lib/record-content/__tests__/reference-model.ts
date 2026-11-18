/**
 * Reference model for the record-content cache: a server with its own truth, three sessions in two
 * orgs, three surfaces, eight writer kinds, revocation, failures and late answers. The product
 * `RecordContentCache` runs against it on a virtual clock, and an oracle checks invariants I1..I17
 * after every operation.
 *
 * Not a test file (no `.test.`): `cache.generated.test.ts` runs it, and `cache-controls.ts` holds
 * the deliberately broken variants that must each fail the invariant they name.
 *
 * Time: one operation is one tick. The cache's clock is `tick * TICK_MS` (so the 10 minute window is
 * `WINDOW_TICKS` ticks) and `Date.now()` for grants is `tick * GRANT_TICK_MS` (so the 30 s grant
 * lifetime is `GRANT_TTL_TICKS` ticks). The caller sets the system time through `clock.set`.
 */
import { createHash } from 'node:crypto';
import {
  PartyLeftError,
  SessionEndedError,
  type CacheOptions,
  type FetchArgs,
  type FetchResult,
  type RecordContentCache,
  type RecordContentFetcher,
  type Variant,
} from '../cache';
import { mintGrant, RECORD_CONTENT_GRANT_TTL_MS } from '../grant';

export const WINDOW_TICKS = 150;
export const TICK_MS = 4000; // 150 ticks = 600 s, the connector window
export const GRANT_TICK_MS = 1000; // 30 ticks = 30 s, the grant lifetime
export const GRANT_TTL_TICKS = RECORD_CONTENT_GRANT_TTL_MS / GRANT_TICK_MS;
export const CHUNK = 10; // bytes per transport chunk
export const THROTTLE_CHUNKS = 3; // one progress event per three chunks (axios throttles to ~333 ms)
export const THROTTLE_BYTES = CHUNK * THROTTLE_CHUNKS;
export const MODEL_BUDGET = 100;
export const MODEL_MAX_ENTRY = 50;

/** Server variants of the model, for the controls that break the server side. */
export type ServerBreak =
  | ''
  | 'metadata-tag'
  | 'no-bracket'
  | 'no-doc-id'
  | '304-before-access'
  | 'contentrev-in-tag';

/** Harness-side breaks (the surface, not the cache). */
export type HarnessBreak = '' | 'land-own-target' | 'no-throttle-model';

export interface ModelBreaks {
  server: ServerBreak;
  harness: HarnessBreak;
}

export const NO_BREAKS: ModelBreaks = { server: '', harness: '' };

export type CacheFactory = (options: CacheOptions) => RecordContentCache;

export interface Clock {
  /** Sets `Date.now()` to this many ms (a fake system time). */
  set(ms: number): void;
}

// ───────────────────────────────────────────────────────────────── plumbing

export class Rng {
  private s: number;
  constructor(seed: number) {
    this.s = seed >>> 0;
  }
  random(): number {
    // mulberry32
    this.s = (this.s + 0x6d2b79f5) >>> 0;
    let t = this.s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  randrange(a: number, b?: number): number {
    return b === undefined ? Math.floor(this.random() * a) : a + Math.floor(this.random() * (b - a));
  }
  choice<T>(xs: readonly T[]): T {
    return xs[Math.floor(this.random() * xs.length)];
  }
}

const drain = () => new Promise<void>((r) => setImmediate(r));

/** Virtual time: `sleep` resolves when `advance` reaches it, so runs are deterministic and fast. */
export class Sim {
  t = 0;
  private seq = 0;
  private q: { at: number; seq: number; fn: () => void }[] = [];
  sleep(ms: number): Promise<void> {
    return new Promise((resolve) => this.q.push({ at: this.t + ms, seq: this.seq++, fn: resolve }));
  }
  async advance(ms: number): Promise<void> {
    const target = this.t + ms;
    for (;;) {
      await drain();
      let best = -1;
      for (let i = 0; i < this.q.length; i++) {
        const c = this.q[i];
        if (c.at <= target && (best < 0 || c.at < this.q[best].at || (c.at === this.q[best].at && c.seq < this.q[best].seq))) best = i;
      }
      if (best < 0) break;
      const [next] = this.q.splice(best, 1);
      this.t = Math.max(this.t, next.at);
      next.fn();
    }
    this.t = target;
    await drain();
  }
  /** Runs until `done()` holds (bounded). */
  async until(done: () => boolean, limit = 100000): Promise<void> {
    for (let i = 0; i < limit && !done(); i++) await this.advance(1);
  }
}

class Surfaced extends Error {
  constructor(readonly status: string) {
    super(`surfaced ${status}`);
  }
}

const never = () => new Promise<never>(() => undefined);

function abortedPromise(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) resolve();
    else signal.addEventListener('abort', () => resolve(), { once: true });
  });
}

function abortError(): Error {
  return new DOMException('aborted', 'AbortError');
}

// ─────────────────────────────────────────────────────────────────── world

interface Rec {
  org: string;
  kind: 'storage' | 'origin' | 'artifact';
  acl: Set<string>;
  bytes: number;
  writeSeq: number;
  mut: number;
  vers: number;
  oetag: number;
  window: number;
  xrev: string;
  size: number;
  crev: string;
  disabled: boolean;
  deleted?: boolean;
  history: [number, number][];
}

interface ServeResult {
  status: number;
  etag?: string | null;
  size?: number;
  bytes_id?: number;
  t0?: number;
  granted?: boolean;
  hdr_size?: number | null;
}

type WriterKind =
  | 'kb_replace'
  | 'kb_replace_fail'
  | 'same_ms_replace'
  | 'reindex'
  | 'presigned_put'
  | 'artifact_ver'
  | 'origin_change'
  | 'origin_sync';

export class World {
  clock = 0;
  byteSeq = 0;
  tagmap = new Map<string, [string, string, number]>();
  liveJobs = new Map<string, Srv[]>();
  violations: string[] = [];
  conversions = 0;
  refetchSameBytes = 0;
  n304 = 0;
  recs: Record<string, Rec> = {};
  syncLog: Record<string, number[]> = {};
  tick = 0;

  constructor(
    readonly r: Rng,
    readonly sim: Sim,
    readonly breaks: ModelBreaks,
    readonly contentrevWorld = false,
  ) {
    const orgs: Record<string, string> = { rec0: 'o1', rec1: 'o1', rec2: 'o1', rec3: 'o1', rec4: 'o1', rec5: 'o2', rec6: 'o1' };
    const kinds: Record<string, Rec['kind']> = { rec4: 'origin', rec6: 'artifact' };
    for (const [rid, org] of Object.entries(orgs)) {
      const users = org === 'o1' ? ['u1', 'u2'] : ['u3'];
      let acl = new Set(users.filter(() => r.random() < 0.85));
      if (acl.size === 0) acl = new Set(users);
      const rec: Rec = {
        org,
        kind: kinds[rid] ?? 'storage',
        acl,
        bytes: this.nb(),
        writeSeq: 0,
        mut: 1,
        vers: 0,
        oetag: 0,
        window: 0,
        xrev: '1',
        size: r.randrange(1, 61),
        crev: 'c0',
        disabled: false,
        history: [],
      };
      rec.history.push([0, rec.bytes]);
      this.recs[rid] = rec;
      this.syncLog[rid] = [];
    }
  }

  nb(): number {
    this.byteSeq += 1;
    return this.byteSeq;
  }

  setBytes(rec: Rec, b: number): void {
    this.clock += 1;
    rec.bytes = b;
    rec.history.push([this.clock, b]);
  }

  bytesBetween(rec: Rec, t0: number): Set<number> {
    const upto = rec.history.filter(([t]) => t <= t0).map(([, b]) => b);
    const out = new Set<number>();
    if (upto.length) out.add(upto[upto.length - 1]);
    for (const [t, b] of rec.history) if (t > t0) out.add(b);
    return out.size ? out : new Set(upto);
  }

  fingerprint(rec: Rec): string | null {
    if (this.breaks.server === 'metadata-tag') return sha(`${rec.xrev}|${rec.size}|${rec.crev}`).slice(0, 12);
    if (rec.kind !== 'storage' || rec.window > 0) return null;
    const parts: (string | number)[] = [rec.writeSeq, rec.mut, rec.vers, rec.size, rec.oetag];
    if (this.breaks.server === 'contentrev-in-tag') parts.push(rec.crev);
    return sha(parts.join('|')).slice(0, 12);
  }

  /** content tag: hash(record id, fingerprint, variant, pipeline). The fingerprint alone is only comparable within one document. */
  tag(fp: string | null, variant: string, rid: string): string | null {
    if (fp === null) return null;
    const doc = this.breaks.server === 'no-doc-id' ? '' : rid;
    const h = sha(`${doc}|${fp}|${variant}|p1`).slice(0, 16);
    return variant === 'original' ? `"${h}"` : `W/"${h}"`;
  }

  async write(kind: WriterKind): Promise<void> {
    const { r, sim } = this;
    const rid =
      ({ artifact_ver: 'rec6', origin_change: 'rec4', origin_sync: 'rec4' } as Record<string, string>)[kind] ??
      r.choice(['rec0', 'rec1', 'rec2', 'rec3', 'rec5']);
    const rec = this.recs[rid];
    if (kind === 'kb_replace' || kind === 'kb_replace_fail' || kind === 'same_ms_replace') {
      rec.window += 1;
      rec.writeSeq += 1;
      await sim.sleep(r.random() * 2);
      this.setBytes(rec, this.nb());
      rec.oetag += 1;
      await sim.sleep(r.random() * 2);
      rec.mut += 1;
      rec.vers += 1;
      if (kind === 'kb_replace') rec.size = r.randrange(1, 61);
      rec.window -= 1;
      rec.writeSeq += 1;
      await sim.sleep(r.random() * 2);
      if (kind === 'kb_replace') rec.xrev = String(Number(rec.xrev) + 1);
    } else if (kind === 'presigned_put') {
      this.setBytes(rec, this.nb());
      rec.oetag += 1;
    } else if (kind === 'reindex') {
      rec.crev = `c${r.randrange(1_000_000)}`;
    } else if (kind === 'artifact_ver' || kind === 'origin_change') {
      this.setBytes(rec, this.nb());
      if (kind === 'origin_change') rec.size = r.randrange(1, 61);
    } else if (kind === 'origin_sync') {
      rec.xrev = String(Number(rec.xrev) + 1);
      this.syncLog[rid].push(this.tick);
    }
  }

  /** The server's handling of GET stream/record: refusals, then the conditional, then the body. */
  async serve(org: string, user: string, rid: string, variant: string, inm: string | null): Promise<ServeResult> {
    const { r, sim } = this;
    const rec = this.recs[rid];
    await sim.sleep(r.random() * 2);
    if (this.breaks.server === '304-before-access' && inm) {
      const t = this.tag(this.fingerprint(rec), variant, rid);
      if (t && t === inm) {
        this.n304 += 1;
        if (!rec.acl.has(user) || rec.org !== org) {
          this.violations.push(`I8 the server answered 304 for ${rid} to ${user}, who has no access`);
        }
        return { status: 304, t0: this.clock, granted: rec.acl.has(user) && rec.org === org };
      }
    }
    if (rec.org !== org) return { status: 404 };
    if (!rec.acl.has(user)) return { status: 403 };
    if (rec.deleted) return { status: 404 };
    if (rec.disabled) return { status: 409 };
    const t0 = this.clock;
    const fp1 = this.fingerprint(rec);
    const t1 = this.tag(fp1, variant, rid);
    if (inm && t1 && inm === t1) {
      this.n304 += 1;
      return { status: 304, t0, granted: true };
    }
    await sim.sleep(r.random() * 2);
    const b = rec.bytes;
    if (this.contentrevWorld && inm && this.etagBytes(inm) === b) this.refetchSameBytes += 1;
    if (variant === 'pdf') {
      this.conversions += 1;
      await sim.sleep(r.random() * 2);
    }
    await sim.sleep(r.random() * 1);
    const fp2 = this.fingerprint(rec);
    const skipBracket = this.breaks.server === 'no-bracket' || this.breaks.server === 'metadata-tag';
    const etag = t1 && (skipBracket || fp1 === fp2) ? t1 : null;
    if (etag) {
      const prev = this.tagmap.get(etag);
      if (!prev) this.tagmap.set(etag, [rid, variant, b]);
      else if (prev[0] !== rid || prev[1] !== variant || prev[2] !== b) {
        this.violations.push(`I7b tag ${etag} named two byte sequences`);
      }
    }
    const chunked = rec.kind !== 'storage' || variant === 'pdf'; // a streamed answer has no Content-Length
    return { status: 200, etag, size: rec.size, bytes_id: b, t0, granted: true, hdr_size: chunked ? null : rec.size };
  }

  /** GET record details with the content-open intent: access, then the connector state. */
  details(org: string, user: string, rid: string): number {
    const rec = this.recs[rid];
    if (rec.org !== org || !rec.acl.has(user)) return rec.org !== org ? 404 : 403;
    if (rec.deleted) return 404;
    if (rec.disabled) return 409;
    return 200;
  }

  label(rid: string): string | null {
    const rec = this.recs[rid];
    return rec.kind === 'origin' ? `${rec.xrev}|${rec.crev}` : null;
  }

  /** What `KnowledgeBaseApi.getRecordDetailsForOpen` does: the content-open call, a grant minted from its own answer. */
  detailsForOpen(org: string, user: string, rid: string): { status: number; grant: unknown } {
    const st = this.details(org, user, rid);
    if (st !== 200) return { status: st, grant: null };
    const label = this.label(rid);
    return { status: 200, grant: label !== null ? mintGrant(rid, label, `${org}|${user}`) : null };
  }

  /** A details call WITHOUT the header: access only, no connector-state refusals. Returns a plain object. */
  detailsMeta(org: string, user: string, rid: string): { status: number; object: unknown } {
    const rec = this.recs[rid];
    if (rec.org !== org || !rec.acl.has(user)) return { status: rec.org !== org ? 404 : 403, object: null };
    return { status: 200, object: { label: this.label(rid) } };
  }

  private etagBytes(etag: string): number | null {
    return this.tagmap.get(etag)?.[2] ?? null;
  }
}

function sha(s: string): string {
  return createHash('sha256').update(s).digest('hex');
}

interface Srv {
  abandoned: boolean;
  got: number;
  key: string;
  gen: number;
  epoch: number;
}

// ──────────────────────────────────────────────────────────── randomised run

export interface RunStats {
  opens: number;
  inplace: number;
  retarget: number;
  surfaced: number;
  labelHits: number;
}

export interface RunOptions {
  seed: number;
  ops: number;
  makeCache: CacheFactory;
  clock: Clock;
  breaks?: ModelBreaks;
  contentrevWorld?: boolean;
  /** Stop at the first violation (the default). */
  stopOnViolation?: boolean;
}

interface Surface {
  lease: { release(): void; url: string; blob: Blob } | null;
  shown: [string, string] | null;
  loading: [string, string] | null;
  cancel: (() => void) | null;
  token: object | null;
  gen: number;
  owner: string | null;
  target: number;
  view: number;
  seq: number;
  shownUrl: string;
}

export async function runWorld(opts: RunOptions): Promise<{ world: World; stats: RunStats }> {
  const breaks = opts.breaks ?? NO_BREAKS;
  const r = new Rng(opts.seed);
  const sim = new Sim();
  const w = new World(r, sim, breaks, opts.contentrevWorld ?? false);
  const gen = { v: 0 };
  const sessions: [string, string][] = [
    ['o1', 'u1'],
    ['o1', 'u2'],
    ['o2', 'u3'],
  ];
  let sess: [string, string] = r.choice(sessions);
  const liveUrls = new Set<string>();
  let created = 0;
  // eslint-disable-next-line prefer-const -- the fetcher closes over it before the cache exists
  let cache!: RecordContentCache;
  const metas = new Map<Blob, { bytes_id: number; t0: number; granted: boolean; etag: string | null }>();
  const setTick = (t: number) => {
    w.tick = t;
    opts.clock.set(1_700_000_000_000 + t * GRANT_TICK_MS);
  };
  setTick(0);

  const ownerNow = () => `${sess[0]}|${sess[1]}`;

  const fetcher: RecordContentFetcher = async (args: FetchArgs): Promise<FetchResult> => {
    const { recordId: rid, variant, ifNoneMatch: inm, signal, onProgress } = args;
    const [org, user] = sess; // the token is read when the request is sent
    const key = `${org}|${user}|${rid}|${variant}`;
    const srv: Srv = { abandoned: false, got: 0, key, gen: gen.v, epoch: cache.fenceEpoch() };
    const jk = `${srv.gen}|${srv.epoch}|${key}|${args.label ?? ''}`;
    signal.addEventListener('abort', () => (srv.abandoned = true), { once: true });
    const live = (w.liveJobs.get(jk) ?? []).filter((x) => !x.abandoned);
    if (live.length) w.violations.push(`I1 second live server job for ${key}`);
    if (!w.liveJobs.has(jk)) w.liveJobs.set(jk, []);
    w.liveJobs.get(jk)!.push(srv);
    const done = () => {
      const l = w.liveJobs.get(jk);
      const i = l ? l.indexOf(srv) : -1;
      if (l && i >= 0) l.splice(i, 1);
    };
    if (r.random() < 0.03) {
      done();
      throw new Surfaced('network');
    }
    let finished = false;
    const server = w.serve(org, user, rid, variant, inm).then((v) => {
      finished = true;
      return v;
    });
    server.catch(() => undefined);
    try {
      return await clientSide(server, () => finished, srv, signal, onProgress, inm);
    } finally {
      if (!finished) void server.then(done, done); // the server job runs on after the client left (I1)
      else done();
    }
  };

  async function clientSide(
    server: Promise<ServeResult>,
    isDone: () => boolean,
    srv: Srv,
    signal: AbortSignal,
    onProgress: FetchArgs['onProgress'],
    inm: string | null,
  ): Promise<FetchResult> {
    const ab = abortedPromise(signal);
    const first = await Promise.race([server.then(() => 'srv' as const), ab.then(() => 'ab' as const)]);
    if (first === 'ab' && !isDone()) {
      if (r.random() < 0.2) return toResult(await server, inm); // already on its way: resolves after the abort
      throw abortError();
    }
    const res = await server;
    if (srv.gen !== gen.v || srv.epoch !== cache.fenceEpoch()) {
      if (r.random() < 0.5) await never(); // apiClient drops an ended session's answer: it never settles
    }
    if (res.status === 200) {
      let got = 0;
      let n = 0;
      const size = res.size!;
      while (got < size) {
        await Promise.race([ab, sim.sleep(0.5)]);
        if (signal.aborted) {
          if (r.random() < 0.8) throw abortError();
          break;
        }
        got = Math.min(size, got + CHUNK);
        n += 1;
        srv.got = got;
        // axios throttles onDownloadProgress: the first event at once, then one per window, plus the last
        if (n === 1 || n % THROTTLE_CHUNKS === 0 || got >= size || breaks.harness === 'no-throttle-model') {
          onProgress({
            loaded: got,
            status: 200,
            etag: res.etag ?? null,
            // @ts-expect-error extra field read only by the count-content-length control
            contentLength: res.hdr_size ?? null,
          });
        }
      }
    }
    return toResult(res, inm);
  }

  function toResult(res: ServeResult, inm: string | null): FetchResult {
    if (res.status === 304) {
      for (const m of metas.values()) {
        if (inm !== null && m.etag === inm) {
          m.t0 = res.t0 ?? m.t0;
          m.granted = true;
        }
      }
      return { status: 304 };
    }
    if (res.status !== 200) throw new Surfaced(String(res.status));
    const blob = new Blob([new Uint8Array(res.size!)], { type: 'application/pdf' });
    metas.set(blob, { bytes_id: res.bytes_id!, t0: res.t0 ?? 0, granted: !!res.granted, etag: res.etag ?? null });
    return { status: 200, blob, etag: res.etag ?? null, mime: 'application/pdf' };
  }

  cache = opts.makeCache({
    fetcher,
    owner: ownerNow,
    budgetBytes: () => MODEL_BUDGET,
    maxEntryBytes: () => MODEL_MAX_ENTRY,
    now: () => w.tick * TICK_MS,
    createObjectURL: () => {
      created += 1;
      const u = `blob:${created}`;
      liveUrls.add(u);
      return u;
    },
    revokeObjectURL: (u) => {
      if (!liveUrls.has(u)) throw new Error(`double revoke ${u}`);
      liveUrls.delete(u);
    },
    onOwnerChange: () => cache.clear(),
  });

  const surfaces: Surface[] = Array.from({ length: 3 }, () => ({
    lease: null,
    shown: null,
    loading: null,
    cancel: null,
    token: null,
    gen: 0,
    owner: null,
    target: 0,
    view: 0,
    seq: 0,
    shownUrl: '',
  }));
  const stats: RunStats = { opens: 0, inplace: 0, retarget: 0, surfaced: 0, labelHits: 0 };
  let tid = 0;
  let sessSeq = 0;
  const grantPool: { key: string; grant: { mintedAt: number }; rid: string }[] = [];
  const pending: Promise<unknown>[] = [];
  let settled = 0;
  const track = (p: Promise<unknown>) => {
    pending.push(p);
    void p.then(
      () => (settled += 1),
      () => (settled += 1),
    );
  };

  function check(): void {
    const snap = cache.snapshot();
    const st = cache.stats();
    if (st.unpinnedBytes > MODEL_BUDGET) w.violations.push(`I2 unpinned ${st.unpinnedBytes} > budget`);
    const curOwner = ownerNow();
    for (const s of surfaces) {
      if (s.lease && s.seq === sessSeq && !liveUrls.has(s.shownUrl)) w.violations.push('I3 shown url revoked');
      if (s.lease && s.seq === sessSeq && s.view !== s.target) {
        w.violations.push(`I14 surface shows target ${s.view} but the user last chose ${s.target}`);
      }
    }
    for (const e of snap.entries) {
      if (e.epoch !== snap.epoch) w.violations.push('I5 entry from an ended session');
      if (snap.owner === curOwner && !e.key.startsWith(`${curOwner}|`)) {
        w.violations.push(`I5 entry of a previous owner kept: ${e.key}`);
      }
    }
    const jobsByKey = new Map(snap.jobs.filter((j) => !j.aborted).map((j) => [j.key, j]));
    let real = 0;
    let nOrph = 0;
    for (const lst of w.liveJobs.values()) {
      for (const x of lst) {
        const j = jobsByKey.get(x.key);
        if (j && j.waiters === 0 && !x.abandoned && x.gen === gen.v) {
          real += x.got;
          if (x.got) nOrph += 1;
        }
      }
    }
    // received bytes of loads nobody waits for: the budget plus one throttle window per such load
    if (real > MODEL_BUDGET + THROTTLE_BYTES * nOrph) {
      w.violations.push(`I13 orphaned bytes received in flight ${real} > budget + ${THROTTLE_BYTES} x ${nOrph}`);
    }
    for (const j of snap.jobs) {
      if (!j.aborted && j.waiters === 0 && j.unkept && j.variant === 'original') {
        w.violations.push(`I16 an unwanted non-retainable original is still downloading: ${j.key}`);
      }
    }
  }

  async function open(si: number, rid: string, variant: Variant): Promise<void> {
    const s = surfaces[si];
    tid += 1;
    const myTid = tid;
    const curOwner = ownerNow();
    if (s.shown && s.shown[0] === rid && s.shown[1] === variant && s.lease && s.gen === gen.v && s.seq === sessSeq) {
      stats.inplace += 1;
      s.target = s.view = myTid; // in place: re-target the shown document now
      return;
    }
    if (s.loading && s.loading[0] === rid && s.loading[1] === variant && s.gen === gen.v && s.seq === sessSeq) {
      stats.retarget += 1;
      s.target = myTid; // same record while loading: update the target, no second load
      return;
    }
    if (s.lease) {
      s.lease.release();
      s.lease = null;
      s.shown = null;
    }
    const myGen = gen.v;
    const [org, user] = sess;
    const mySeq = sessSeq;
    const token = {};
    let cancelled = false;
    let pend: ReturnType<RecordContentCache['acquire']> | null = null;
    s.loading = [rid, variant];
    s.token = token;
    s.cancel = () => {
      cancelled = true;
      pend?.leave();
    };
    s.gen = myGen;
    s.owner = curOwner;
    s.target = myTid;
    s.seq = mySeq;
    stats.opens += 1;
    const key = `${org}|${user}|${rid}|${variant}`;
    const rec = w.recs[rid];
    const fault = r.random();
    const buggy = fault < 0.08; // a caller that skips the details call
    const headerless = fault >= 0.08 && fault < 0.13; // details from a call without the content-open header
    const stale = fault >= 0.13 && fault < 0.18; // an old grant, past its lifetime
    let grant: unknown = null;
    if (!(buggy || headerless || stale)) {
      await sim.sleep(r.random() * 1);
      const d = w.detailsForOpen(org, user, rid);
      grant = d.grant;
      if (d.status !== 200) {
        cache.evictRecord(rid);
        stats.surfaced += 1;
        if (s.token === token) s.loading = null;
        const left = cache.snapshot().entries.filter((e) => e.key.startsWith(`${org}|${user}|${rid}|`));
        if (left.length) w.violations.push(`I17 ${rid} still cached for ${user} after a details refusal`);
        return;
      }
    }
    const label = w.label(rid);
    let ticket: unknown;
    if (buggy) ticket = label ?? undefined;
    else if (headerless) ticket = w.detailsMeta(org, user, rid).object;
    else if (stale) {
      const old = grantPool.find((g) => g.key === key && Date.now() - g.grant.mintedAt > RECORD_CONTENT_GRANT_TTL_MS);
      ticket = old ? old.grant : (label ?? undefined);
    } else ticket = grant ?? undefined;
    const hadJob = cache.snapshot().jobs.some((j) => j.key === key);
    pend = cache.acquire(rid, variant, ticket as never);
    const isHit = !cache.snapshot().jobs.some((j) => j.key === key);
    if (cancelled) pend.leave();
    let lease;
    try {
      lease = await pend.promise;
    } catch (e) {
      if (e instanceof PartyLeftError) return;
      if (e instanceof Surfaced) {
        stats.surfaced += 1;
        if (s.token === token) s.loading = null;
        return;
      }
      if (!cancelled && myGen === gen.v && sessSeq === mySeq && !(e instanceof SessionEndedError && false)) {
        w.violations.push(`I9 open failed though never cancelled: ${String(e)}`);
      }
      return;
    }
    if (myGen !== gen.v || s.token !== token || sessSeq !== mySeq) {
      lease.release(); // superseded: an ended session, an owner change, or a newer open
      return;
    }
    const meta = metas.get(lease.blob);
    if (!meta) {
      w.violations.push('harness: a lease holds a blob the server never produced');
      return;
    }
    if (!w.bytesBetween(rec, meta.t0).has(meta.bytes_id)) {
      w.violations.push(`I7 ${rid} showed bytes ${meta.bytes_id} the server no longer held since t0`);
    }
    if (!meta.granted || rec.org !== org) w.violations.push(`I8 ${rid} shown to ${user} without an access-checked answer`);
    if (isHit && !hadJob) {
      if (buggy || headerless || stale) {
        const what = buggy ? 'no details call' : headerless ? 'details without the content-open header' : 'a stale grant';
        w.violations.push(`I8b ${rid} served from memory to an open with ${what}`);
      }
      stats.labelHits += 1;
      const fetched = hitFetchedTick(key);
      const age = w.tick - fetched;
      const synced = w.syncLog[rid].filter((t) => t > fetched);
      if (age > WINDOW_TICKS) w.violations.push(`I12 ${rid} reused from memory ${age} ticks after its fetch (window ${WINDOW_TICKS})`);
      else if (synced.length) w.violations.push(`I12 ${rid} reused from memory although its label moved at tick ${synced[0]}`);
    }
    s.lease = lease;
    s.shown = [rid, variant];
    s.loading = null;
    s.shownUrl = lease.url;
    s.view = breaks.harness === 'land-own-target' ? myTid : s.target; // land on the CURRENT target
  }

  // A label hit takes the entry as it is; read its fetch tick before the open that used it changes anything.
  const fetchedTicks = new Map<string, number>();
  function hitFetchedTick(key: string): number {
    return fetchedTicks.get(key) ?? 0;
  }
  const origAcquire = cache.acquire.bind(cache);
  cache.acquire = (rid: string, variant: Variant, grant?: never) => {
    const [org, user] = sess;
    const key = `${org}|${user}|${rid}|${variant}`;
    const e = cache.snapshot().entries.find((x) => x.key === key);
    if (e) fetchedTicks.set(key, e.fetchedAt / TICK_MS);
    return origAcquire(rid, variant, grant);
  };

  const kinds: WriterKind[] = opts.contentrevWorld
    ? ['reindex']
    : ['kb_replace', 'kb_replace_fail', 'same_ms_replace', 'reindex', 'presigned_put', 'artifact_ver', 'origin_change', 'origin_sync'];

  for (let i = 0; i < opts.ops; i++) {
    setTick(w.tick + 1);
    const p = r.random();
    const cw = opts.contentrevWorld ?? false;
    if (p < 0.45) {
      const si = r.randrange(3);
      const own = Object.entries(w.recs).filter(([, v]) => v.org === sess[0]).map(([k]) => k);
      const rid = r.random() < 0.85 ? r.choice(own) : r.choice(Object.keys(w.recs));
      const variant: Variant = r.random() < 0.2 ? 'pdf' : 'original';
      track(open(si, rid, variant));
    } else if (p < 0.55) {
      const s = surfaces[r.randrange(3)]; // unmount: the surface's interest ends
      s.cancel?.();
      s.lease?.release();
      s.lease = null;
      s.shown = null;
      s.loading = null;
      s.token = null;
    } else if (p < 0.7) {
      track(w.write(r.choice(kinds)));
    } else if (p < 0.715 && !cw) {
      const rec4 = w.recs.rec4; // the connector of the origin record: disabled or deleted for a while
      if (rec4.disabled || rec4.deleted) rec4.disabled = rec4.deleted = false;
      else if (r.random() < 0.3) {
        if (r.random() < 0.5) rec4.disabled = true;
        else rec4.deleted = true;
      }
    } else if (p < 0.74 && !cw) {
      const rec = w.recs[r.choice(Object.keys(w.recs))];
      const u = r.choice(['u1', 'u2', 'u3']);
      if (rec.acl.has(u)) rec.acl.delete(u);
      else rec.acl.add(u);
    } else if (p < 0.78 && !cw) {
      sess = r.choice(sessions.filter((x) => x !== sess)); // the token owner changes, no session end
      sessSeq += 1;
    } else if (p < 0.8 && !cw) {
      gen.v += 1;
      sessSeq += 1;
      const ended = new Set(liveUrls);
      cache.clear(); // session end; the harness releases NO lease
      const outlived = [...liveUrls].filter((u) => ended.has(u));
      if (outlived.length) w.violations.push(`I4 ${outlived.length} object URLs outlived the session`);
      for (const s of surfaces) {
        s.lease = null;
        s.shown = null;
        s.loading = null;
        s.token = null;
        s.cancel = null;
      }
      sess = r.choice(sessions);
    } else if (p < 0.805 && !cw) {
      const [org, user] = sess; // a prefetch of content-open details whose grant is never used in time
      const d = w.detailsForOpen(org, user, 'rec4');
      if (d.grant) grantPool.push({ key: `${org}|${user}|rec4|original`, grant: d.grant as { mintedAt: number }, rid: 'rec4' });
    } else {
      await sim.advance(1);
    }
    check();
    if (w.violations.length && (opts.stopOnViolation ?? true)) break;
  }
  await sim.until(() => settled >= pending.length);
  check();
  gen.v += 1;
  cache.clear();
  if (liveUrls.size) w.violations.push(`I4 leaked object urls at the end: ${liveUrls.size}`);
  return { world: w, stats };
}

// ──────────────────────────────────────────────────────── deterministic cases

export interface DetResult {
  name: string;
  ok: boolean;
  msg: string;
}

class Urls {
  live = new Set<string>();
  n = 0;
  create = () => {
    this.n += 1;
    const u = `b${this.n}`;
    this.live.add(u);
    return u;
  };
  revoke = (u: string) => {
    this.live.delete(u);
  };
}

function res200(size: number, etag: string | null): FetchResult {
  return { status: 200, blob: new Blob([new Uint8Array(size)], { type: 'application/pdf' }), etag, mime: 'application/pdf' };
}

export async function deterministic(makeCache: CacheFactory, clock: Clock, breaks: ModelBreaks = NO_BREAKS): Promise<DetResult[]> {
  const out: DetResult[] = [];
  const sim = new Sim();
  const base = 1_700_000_000_000;
  const owner = 'o|u';
  const mk = (extra: Partial<CacheOptions> & { fetcher: RecordContentFetcher }, urls = new Urls(), budget = 1000, maxEntry = 500) => {
    const c: RecordContentCache = makeCache({
      owner: () => owner,
      budgetBytes: () => budget,
      maxEntryBytes: () => maxEntry,
      createObjectURL: urls.create,
      revokeObjectURL: urls.revoke,
      onOwnerChange: () => c.clear(),
      ...extra,
    });
    return { c, urls };
  };
  clock.set(base);

  const dbg = (m: string) => { if (process.env.MODEL_DEBUG) console.log('det', m); };
  dbg('I6');
  // I6: 50 concurrent opens of one key -> 1 server job, 1 URL
  {
    let calls = 0;
    const { c } = mk({
      fetcher: async () => {
        calls += 1;
        await sim.sleep(1);
        return res200(10, '"e"');
      },
    });
    const ps = Array.from({ length: 50 }, () => c.acquire('k', 'original'));
    await sim.advance(5);
    const ls = await Promise.all(ps.map((p) => p.promise));
    const urls = new Set(ls.map((l) => l.url));
    out.push({ name: 'I6', ok: calls === 1 && urls.size === 1, msg: `50 concurrent opens -> server jobs=${calls}, object urls=${urls.size}` });
  }

  dbg('I9');
  // I9: the first party leaves, a second joins before the load settles -> it gets the lease, 1 job
  {
    let calls = 0;
    const { c } = mk({
      fetcher: async ({ signal }) => {
        calls += 1;
        await Promise.race([sim.sleep(5), abortedPromise(signal)]);
        if (signal.aborted) throw abortError();
        return res200(10, '"e"');
      },
    });
    const p1 = c.acquire('R', 'pdf');
    await sim.advance(1);
    p1.leave();
    await sim.advance(0);
    const p2 = c.acquire('R', 'pdf');
    await sim.advance(10);
    const r2 = await Promise.allSettled([p1.promise, p2.promise]);
    out.push({
      name: 'I9',
      ok: r2[1].status === 'fulfilled' && calls === 1,
      msg: `leave then join during the load -> second open ${r2[1].status}, server jobs=${calls}`,
    });
  }

  dbg('I10');
  // I10: a 304 for If-None-Match e0 must not re-label an entry that meanwhile holds other bytes
  {
    let n = 0;
    const { c } = mk({
      fetcher: async ({ ifNoneMatch }) => {
        n += 1;
        await sim.sleep(1);
        if (ifNoneMatch) return { status: 304 };
        return res200(5, '"e0"');
      },
    });
    const fill = c.acquire('k', 'original');
    await sim.advance(3);
    (await fill.promise).release(); // retained under e0
    const job = c.acquire('k', 'original'); // sends If-None-Match: "e0"
    const entries = (c as unknown as { entries: Map<string, { etag: string | null; blob: Blob }> }).entries;
    const entry = entries.get('o|u|k|original')!;
    entry.etag = '"e_new"'; // the entry changed while the validator was out
    entry.blob = new Blob([new Uint8Array(9)]);
    await sim.advance(5);
    const l = await job.promise;
    out.push({
      name: 'I10',
      ok: l.blob !== entry.blob,
      msg: `304 for e0 over an entry now holding e_new -> fetches=${n}, lease shows the changed entry: ${l.blob === entry.blob}`,
    });
  }

  // I1b: an abandoned job never retries. Headers show an unwanted original (abandoned); the fetch then answers 304 over
  // a changed entry, which would make an un-abandoned job ask once more in full, outside single flight.
  dbg('I1b');
  {
    let phase: 'fill' | 'probe' = 'fill';
    let probeCalls = 0;
    let cRef: RecordContentCache | null = null;
    const { c } = mk({
      fetcher: async (args) => {
        if (phase === 'fill') {
          await sim.sleep(1);
          return res200(5, '"old"');
        }
        probeCalls += 1;
        if (probeCalls === 1) {
          await sim.sleep(1); // the only waiter has left by now
          const entries = (cRef as unknown as { entries: Map<string, { etag: string | null }> }).entries;
          entries.get('o|u|Z|original')!.etag = '"changed"';
          args.onProgress({ loaded: 5, status: 200, etag: null }); // nobody waits: abandoned here
          return { status: 304 };
        }
        return res200(5, '"x"');
      },
    });
    cRef = c;
    const fill = c.acquire('Z', 'original');
    await sim.advance(3);
    (await fill.promise).release();
    phase = 'probe';
    c.acquire('Z', 'original').leave();
    await sim.advance(20);
    out.push({ name: 'I1b', ok: probeCalls === 1, msg: `abandoned job answered 304: fetches=${probeCalls} (a retry would run outside single flight)` });
  }

  dbg('I15');
  // I15: A's load is in flight, the token owner becomes B with no session end, B opens; A's answer resolves after
  // its abort. It must be neither kept nor leased.
  {
    let who = 'oA|uA';
    const urls = new Urls();
    const { c } = mk(
      {
        owner: () => who,
        fetcher: async ({ recordId }) => {
          await sim.sleep(10); // already on its way: resolves despite the abort
          return res200(10, recordId === 'R' ? '"a1"' : '"b1"');
        },
      },
      urls,
    );
    const ta = c.acquire('R', 'original');
    await sim.advance(1);
    who = 'oB|uB';
    const tb = c.acquire('S', 'original');
    await sim.advance(20);
    const lb = await tb.promise;
    void lb.url;
    const ra = await Promise.allSettled([ta.promise]);
    const aKept = c.snapshot().entries.filter((e) => e.key.startsWith('oA|')).map((e) => e.key);
    out.push({
      name: 'I15',
      ok: aKept.length === 0 && ra[0].status === 'rejected' && urls.live.size === 1,
      msg: `owner change mid-load: A kept=${JSON.stringify(aKept)}, A leased=${ra[0].status === 'fulfilled'}, live URLs=${urls.live.size}`,
    });
  }

  dbg('I13');
  // I13: 10 superseded non-retainable 40-byte originals in one surface: the unwanted ones are aborted at their headers
  {
    let inflight = 0;
    let peak = 0;
    let cRef: RecordContentCache | null = null;
    const { c } = mk(
      {
        fetcher: async ({ signal, onProgress }) => {
          await sim.sleep(1);
          onProgress({ loaded: 10, status: 200, etag: null });
          if (signal.aborted) throw abortError();
          inflight += 40;
          peak = Math.max(peak, inflight);
          try {
            await Promise.race([sim.sleep(30), abortedPromise(signal)]);
            if (signal.aborted) throw abortError();
            return res200(40, null);
          } finally {
            inflight -= 40;
          }
        },
      },
      new Urls(),
      100,
      50,
    );
    cRef = c;
    void cRef;
    const ps: ReturnType<RecordContentCache['acquire']>[] = [];
    let prev: ReturnType<RecordContentCache['acquire']> | null = null;
    for (let i = 0; i < 10; i++) {
      const p = c.acquire(`R${i}`, 'original');
      ps.push(p);
      prev?.leave();
      prev = p;
      await sim.advance(2);
    }
    await sim.advance(100);
    await Promise.allSettled(ps.map((p) => p.promise));
    out.push({ name: 'I13', ok: peak <= 100 + 40, msg: `10 superseded non-retainable 40-byte originals: peak bytes in flight=${peak} (budget 100 + the one shown)` });
  }

  dbg('I13c');
  // I13c: 10 superseded chunked connector originals (labelled, so kept: they are not aborted at headers), 120 bytes
  // each, throttled progress. Received bytes of loads nobody waits for stay within the budget plus one window each.
  {
    const recv = new Map<string, number>();
    let over = -Infinity;
    let overNoThrottle = -Infinity;
    let cRef: RecordContentCache | null = null;
    const { c } = mk(
      {
        fetcher: async ({ recordId, signal, onProgress }) => {
          await sim.sleep(1);
          let got = 0;
          try {
            while (got < 120) {
              await Promise.race([sim.sleep(12), abortedPromise(signal)]);
              if (signal.aborted) throw abortError();
              got += 10;
              recv.set(recordId, got);
              const snap = cRef!.snapshot().jobs;
              const orph = [...recv.keys()].filter((k) => snap.some((j) => j.key === `${owner}|${k}|original` && j.waiters === 0 && !j.aborted));
              const total = [...recv.values()].reduce((a, b) => a + b, 0);
              over = Math.max(over, total - (100 + 120 + THROTTLE_BYTES * orph.length));
              overNoThrottle = Math.max(overNoThrottle, total - (100 + 120));
              if (got === 10 || (got / 10) % THROTTLE_CHUNKS === 0 || got >= 120 || breaks.harness === 'no-throttle-model') {
                const extra = { contentLength: null };
                onProgress({ loaded: got, status: 200, etag: null, ...extra } as never);
              }
            }
            return res200(120, null);
          } finally {
            recv.delete(recordId);
          }
        },
      },
      new Urls(),
      100,
      500,
    );
    cRef = c;
    const ps: ReturnType<RecordContentCache['acquire']>[] = [];
    let prev: ReturnType<RecordContentCache['acquire']> | null = null;
    for (let i = 0; i < 10; i++) {
      const p = c.acquire(`R${i}`, 'original', mintGrant(`R${i}`, `xrev${i}|c`, owner));
      ps.push(p);
      prev?.leave();
      prev = p;
      await sim.advance(2);
    }
    await sim.advance(500);
    await Promise.allSettled(ps.map((p) => p.promise));
    out.push({
      name: 'I13c',
      ok: over <= 0,
      msg: `10 superseded chunked 120-byte connector originals, throttled progress: max(received - (budget 100 + shown 120 + ${THROTTLE_BYTES} x orphans))=${over} (<= 0 holds the bound); without the throttle term the overshoot would be ${overNoThrottle}`,
    });
  }

  dbg('I8c');
  // I8c: a grant older than its lifetime opens no hit, even unused and for the right record and label
  {
    let calls = 0;
    const { c } = mk({
      fetcher: async () => {
        calls += 1;
        await sim.sleep(1);
        return res200(5, null);
      },
    });
    clock.set(base);
    const first = c.acquire('C', 'original', mintGrant('C', 'x1|c1', owner));
    await sim.advance(5);
    (await first.promise).release(); // filled under a fresh grant
    const staleGrant = mintGrant('C', 'x1|c1', owner); // minted now, used after its lifetime
    clock.set(base + (GRANT_TTL_TICKS + 1) * GRANT_TICK_MS);
    const second = c.acquire('C', 'original', staleGrant);
    const hit = !c.snapshot().jobs.some((j) => j.key === `${owner}|C|original`);
    await sim.advance(5);
    await second.promise.then((l) => l.release(), () => undefined);
    clock.set(base);
    out.push({ name: 'I8c', ok: !hit && calls === 2, msg: `stale grant (age ${GRANT_TTL_TICKS + 1} ticks > lifetime ${GRANT_TTL_TICKS}): hit=${hit}, fetches=${calls}` });
  }
  return out;
}
