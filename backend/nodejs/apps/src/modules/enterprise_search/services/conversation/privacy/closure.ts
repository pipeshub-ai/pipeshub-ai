import { createHash } from 'crypto';

/**
 * The closure of an answer version: everything a reader must pass to see that version as an earlier answer of another
 * one. The reader check used to gather it on every read by walking the version's parents a level at a
 * time; a closure gathers it once, when the version is written, from its label and its parents' closures.
 *
 * A closure is a fold of the labels it covers. Who asked each of them matters (an asker reads past her own missing
 * label, her own deleted sources and projects, and her own marks), so record ids, projects, marks and labels that cannot
 * be relied on are kept apart by asker. Everything else is one union: personal results, agents and chat uploads.
 *
 * What can change after the version was written is not folded in but kept as a reference the read judges:
 * - an earlier answer given as text that was shared then (`alts`): a reader the share covers needs nothing of it;
 * - an answer from before labels given as text (`legacy`): judged by the per-record view of its turn;
 * - a shared copy (`rows`, or `legacy` when given as text): read in its chat while it, or the copy that took its place,
 *   is there, and otherwise as the answer it was made from;
 * - a question or a note (`rows`): it reads only while it is not removed;
 * - a file the run read (`files`): its writer is found in the chats the read covers;
 * - a file saved before the rules, which no run wrote (`legacyFiles`): judged by who could read its chat then.
 * An earlier answer given as text that was not shared then is folded in; a share made later is noticed by the read
 * (`seen`, `stale`) and the closure is made again.
 */

/** How a part of a closure is told apart by asker: `u:<userId>` for a row that names its asker, `o:<chat>` for one
 * that does not (its chat's owner, as the reading chat says). */
export type AskerKey = string;

export const askerKey = (
  requestedBy: string | undefined,
  sessionId: string | undefined,
): AskerKey =>
  requestedBy !== undefined ? `u:${requestedBy}` : `o:${sessionId ?? ''}`;

/** The labels of one asker in a fold: the ids and projects they name, and whether any names a mark or cannot be relied on. */
export interface AskerPart {
  /** Record and container ids: a reader must be able to open each. */
  ids: Set<string>;
  /** Projects whose instructions they were given: a reader must be able to open each; only the asker reads past a deleted one. */
  projects: Set<string>;
  /** A mark (an administrator's statement about a source): only the asker reads past it. */
  marks: boolean;
  /** A version whose label cannot be relied on, or is missing: only its asker reads past it. */
  unsure: boolean;
}

/** A version given as text, or another row, that the read judges as it is then. */
export interface VersionRef {
  id: string;
  ver: string;
}

const refKey = (ref: VersionRef): string => `${ref.id}@${ref.ver}`;

/** A fold of labels: their union, kept apart by asker where an asker reads differently. */
export class Fold {
  readonly askers = new Map<AskerKey, AskerPart>();
  readonly personal = new Set<string>();
  readonly agents = new Set<string>();
  /** The chats of chat uploads (`direct.chat`), by conversation id. */
  readonly uploads = new Set<string>();
  /** An earlier row it names that does not exist, or a file it names that no run can be found for. */
  broken = false;
  /** Versions given as text that were shared when the fold was made: a share then can still cover the reader. */
  readonly alts = new Map<string, VersionRef>();
  /**
   * Versions folded in whole that were shared when the fold was made (given in full, or shared after a version given
   * as text was folded and the closure made again): a share check stops at a share wherever it meets one.
   */
  readonly held = new Map<string, VersionRef>();
  /** A label in it was searched for as a service identity: never shared. */
  service = false;
  /**
   * The agents that wrote answers in it (`respondingAgentKey`), whatever their labels say. No read asks about them: a
   * share's check does, and asks each agent's own record.
   */
  readonly responders = new Set<string>();
  /** Answers from before labels given as text, by row id. */
  readonly legacy = new Set<string>();
  /** Shared copies, questions and notes it names. */
  readonly rows = new Map<string, VersionRef>();
  /** The runs of the files it names. */
  readonly files = new Set<string>();
  /** Files saved before the rules that it names, by the key of their chat, id and write time (`legacyFileKey`). */
  readonly legacyFiles = new Set<string>();
  /** The chats of the rows folded in: a share or a dropped version there may change it. */
  readonly spans = new Set<string>();

  part(key: AskerKey): AskerPart {
    let part = this.askers.get(key);
    if (!part) {
      part = {
        ids: new Set(),
        projects: new Set(),
        marks: false,
        unsure: false,
      };
      this.askers.set(key, part);
    }
    return part;
  }

  addAlt(ref: VersionRef): void {
    this.alts.set(refKey(ref), ref);
  }

  addRow(ref: VersionRef): void {
    this.rows.set(refKey(ref), ref);
  }

  addHeld(ref: VersionRef): void {
    this.held.set(refKey(ref), ref);
  }

  merge(other: Fold): void {
    for (const [key, part] of other.askers) {
      const mine = this.part(key);
      for (const id of part.ids) mine.ids.add(id);
      for (const p of part.projects) mine.projects.add(p);
      mine.marks = mine.marks || part.marks;
      mine.unsure = mine.unsure || part.unsure;
    }
    for (const p of other.personal) this.personal.add(p);
    for (const a of other.agents) this.agents.add(a);
    for (const u of other.uploads) this.uploads.add(u);
    this.broken = this.broken || other.broken;
    this.service = this.service || other.service;
    for (const [key, ref] of other.alts) this.alts.set(key, ref);
    for (const [key, ref] of other.held) this.held.set(key, ref);
    for (const key of other.responders) this.responders.add(key);
    for (const id of other.legacy) this.legacy.add(id);
    for (const [key, ref] of other.rows) this.rows.set(key, ref);
    for (const f of other.files) this.files.add(f);
    for (const f of other.legacyFiles) this.legacyFiles.add(f);
    for (const s of other.spans) this.spans.add(s);
  }

  /** Ids a reader must be able to open, counted once per asker that names them. */
  get size(): number {
    let n = 0;
    for (const part of this.askers.values()) n += part.ids.size;
    return n;
  }

  /** What this fold holds that none of `bases` does: what a closure stores beside the one it is built on. */
  without(...bases: Fold[]): Fold {
    const out = new Fold();
    const inAny = <T>(
      pick: (f: Fold) => { has(v: T): boolean },
      v: T,
    ): boolean => bases.some((b) => pick(b).has(v));
    for (const [key, part] of this.askers) {
      const theirs = bases
        .map((b) => b.askers.get(key))
        .filter((p): p is AskerPart => p !== undefined);
      const ids = [...part.ids].filter(
        (id) => !theirs.some((p) => p.ids.has(id)),
      );
      const projects = [...part.projects].filter(
        (pid) => !theirs.some((p) => p.projects.has(pid)),
      );
      const marks = part.marks && !theirs.some((p) => p.marks);
      const unsure = part.unsure && !theirs.some((p) => p.unsure);
      if (
        ids.length === 0 &&
        projects.length === 0 &&
        !marks &&
        !unsure &&
        theirs.length > 0
      )
        continue;
      const mine = out.part(key);
      for (const id of ids) mine.ids.add(id);
      for (const pid of projects) mine.projects.add(pid);
      mine.marks = marks;
      mine.unsure = unsure;
    }
    for (const p of this.personal)
      if (!inAny((f) => f.personal, p)) out.personal.add(p);
    for (const a of this.agents)
      if (!inAny((f) => f.agents, a)) out.agents.add(a);
    for (const u of this.uploads)
      if (!inAny((f) => f.uploads, u)) out.uploads.add(u);
    out.broken = this.broken && !bases.some((b) => b.broken);
    out.service = this.service && !bases.some((b) => b.service);
    for (const [key, ref] of this.alts)
      if (!inAny((f) => f.alts, key)) out.alts.set(key, ref);
    for (const [key, ref] of this.held)
      if (!inAny((f) => f.held, key)) out.held.set(key, ref);
    for (const key of this.responders)
      if (!inAny((f) => f.responders, key)) out.responders.add(key);
    for (const id of this.legacy)
      if (!inAny((f) => f.legacy, id)) out.legacy.add(id);
    for (const [key, ref] of this.rows)
      if (!inAny((f) => f.rows, key)) out.rows.set(key, ref);
    for (const r of this.files) if (!inAny((f) => f.files, r)) out.files.add(r);
    for (const r of this.legacyFiles)
      if (!inAny((f) => f.legacyFiles, r)) out.legacyFiles.add(r);
    for (const c2 of this.spans)
      if (!inAny((f) => f.spans, c2)) out.spans.add(c2);
    return out;
  }

  copy(): Fold {
    const out = new Fold();
    out.merge(this);
    return out;
  }

  toStored(): StoredFold {
    const out: StoredFold = {};
    const askers: StoredFold['askers'] = {};
    for (const [key, part] of this.askers) {
      askers[key] = {
        ...(part.ids.size > 0 && { ids: [...part.ids] }),
        ...(part.projects.size > 0 && { projects: [...part.projects] }),
        ...(part.marks && { marks: true }),
        ...(part.unsure && { unsure: true }),
      };
    }
    if (this.askers.size > 0) out.askers = askers;
    if (this.personal.size > 0) out.personal = [...this.personal];
    if (this.agents.size > 0) out.agents = [...this.agents];
    if (this.uploads.size > 0) out.uploads = [...this.uploads];
    if (this.broken) out.broken = true;
    if (this.service) out.service = true;
    if (this.responders.size > 0) out.responders = [...this.responders];
    if (this.alts.size > 0)
      out.alts = [...this.alts.values()].map((r) => [r.id, r.ver]);
    if (this.held.size > 0)
      out.held = [...this.held.values()].map((r) => [r.id, r.ver]);
    if (this.legacy.size > 0) out.legacy = [...this.legacy];
    if (this.rows.size > 0)
      out.rows = [...this.rows.values()].map((r) => [r.id, r.ver]);
    if (this.files.size > 0) out.files = [...this.files];
    if (this.legacyFiles.size > 0) out.legacyFiles = [...this.legacyFiles];
    if (this.spans.size > 0) out.spans = [...this.spans];
    return out;
  }

  /** A fold as stored; undefined when the stored form is not one this build wrote. */
  static fromStored(value: unknown): Fold | undefined {
    if (!isRecord(value)) return undefined;
    const out = new Fold();
    const askers = value.askers;
    if (askers !== undefined) {
      if (!isRecord(askers)) return undefined;
      for (const [key, raw] of Object.entries(askers)) {
        if (!isRecord(raw)) return undefined;
        const ids = raw.ids ?? [];
        const projects = raw.projects ?? [];
        if (!isStrings(ids) || !isStrings(projects)) return undefined;
        const part = out.part(key);
        for (const id of ids) part.ids.add(id);
        for (const pid of projects) part.projects.add(pid);
        part.marks = raw.marks === true;
        part.unsure = raw.unsure === true;
      }
    }
    for (const [field, target] of [
      ['personal', out.personal],
      ['agents', out.agents],
      ['uploads', out.uploads],
      ['responders', out.responders],
      ['legacy', out.legacy],
      ['files', out.files],
      ['legacyFiles', out.legacyFiles],
      ['spans', out.spans],
    ] as const) {
      const list = value[field] ?? [];
      if (!isStrings(list)) return undefined;
      for (const item of list) target.add(item);
    }
    for (const [field, add] of [
      [
        'alts',
        (r: VersionRef): void => {
          out.addAlt(r);
        },
      ],
      [
        'rows',
        (r: VersionRef): void => {
          out.addRow(r);
        },
      ],
      [
        'held',
        (r: VersionRef): void => {
          out.addHeld(r);
        },
      ],
    ] as const) {
      const list = value[field] ?? [];
      if (!Array.isArray(list)) return undefined;
      for (const pair of list as unknown[]) {
        if (!isStrings(pair) || pair.length !== 2) return undefined;
        add({ id: pair[0] as string, ver: pair[1] as string });
      }
    }
    out.broken = value.broken === true;
    out.service = value.service === true;
    return out;
  }
}

/** A fold as a document stores it: only what it holds. */
export interface StoredFold {
  askers?: Record<
    string,
    { ids?: string[]; projects?: string[]; marks?: true; unsure?: true }
  >;
  personal?: string[];
  agents?: string[];
  uploads?: string[];
  broken?: true;
  service?: true;
  responders?: string[];
  alts?: Array<[string, string]>;
  held?: Array<[string, string]>;
  legacy?: string[];
  rows?: Array<[string, string]>;
  files?: string[];
  legacyFiles?: string[];
  spans?: string[];
}

/**
 * Whether a version restricts nobody as far as its stored parts say: the label conditions of the version
 * as an earlier answer (`self`), and of every version below it (`below`). Who shares a row changes over time, so the
 * rows of that tree (`rows`) are read again when the mark is asked.
 */
export interface PubPart {
  self: boolean;
  below: boolean;
  rows: string[];
  /** The chats of `rows`, where their shares are read. */
  chats: string[];
}

/** One version's closure, as the read uses it. */
export interface Closure {
  id: string;
  ver: string;
  /** The chat of the row; absent when the row was read without one. */
  sessionId?: string;
  /** The version as an earlier answer of another: its own label, by its asker, and the files its run read. */
  self: Fold;
  /** What its parents bring: every version it was given, folded, and what the read judges as it is then. */
  rest: Fold;
  /** The parents folded into `rest`, by `id@ver`: a fold that holds one of them holds all of it. */
  folded: string[];
  /** Ids in `self` and `rest`, as counted when it was made: which closure of several is the largest. */
  weight: number;
  /** The chats of the rows `rest` folds in (`rest.spans`), readable without building `rest`. */
  spans: ReadonlySet<string>;
  pub: PubPart;
  /** By chat, the shares (`shareId`) a stored closure took account of; absent on one made by this read. */
  seen?: Record<string, string[]>;
}

/** The most ids a closure holds; past it the closure is not kept, and the version reads as one whose earlier answers
 * could not all be read: hidden. */
export const CLOSURE_IDS_MAX = 50_000;

/** The most rows of a version's tree its public mark is kept for; past it, the version is not public. */
export const PUB_ROWS_MAX = 2_000;

export const closureKey = (id: string, ver: string): string => `${id}@${ver}`;

// ---- stored closures ------------------------------------------------------------------------------------------

/** Bumped whenever what a closure holds, or how it is read, changes: a closure of another rev is made again. */
export const CLOSURE_REV = 7;

/** Closures a stored one is built on, at most, before one is stored whole. */
export const CHAIN_MAX = 32;

/**
 * Two version ids are compared by when they were made (their first four bytes, as every ObjectId), give or take this
 * much: the clocks of the replicas that made them may differ.
 */
export const CLOCK_MARGIN_MS = 15 * 60_000;

/** The shares of one chat a stored closure took account of, at most; past it, the closure is made again on each read. */
export const SHARES_SEEN_MAX = 1_000;

/**
 * A closure as stored: its own part whole, and the rest as what it adds to the closure of a version it was built on
 * (`base`), whose own chain of bases ends in one stored whole. `chain` names every closure of that chain with its
 * digest, so one read fetches them all and a closure made again since is noticed.
 */
export interface StoredClosure {
  _id: string;
  orgId: string;
  sessionId?: string;
  rowId: string;
  ver: string;
  rev: number;
  self: StoredFold;
  add: StoredFold;
  chain: Array<{ key: string; digest: string }>;
  folded: string[];
  /** `Closure.weight` and `Closure.spans`, so a closure is weighed and checked without building it. */
  size: number;
  spans: string[];
  pub: PubPart;
  /** By chat: the shares it took account of (`shareId`). */
  shares: Record<string, string[]>;
  digest: string;
  at: Date;
}

/** A row's shares as the store holds them: on its current version, and archived with earlier ones. */
export interface ShareMarkRow {
  _id: string;
  sessionId: string;
  labelVer?: unknown;
  consent?: unknown;
  labelHistory?: unknown;
}

/** Where closures are kept, and the shares of chats read beside them. */
export interface ClosureStore {
  /** Stored closures by key; a key with none is left out. */
  get(
    orgId: string,
    keys: readonly string[],
  ): Promise<ReadonlyMap<string, StoredClosure>>;
  /** Stores closures, each replacing any stored under its key. */
  put(orgId: string, closures: readonly StoredClosure[]): Promise<void>;
  /** The rows of these chats that carry a share, on their current version or an archived one. */
  shares(
    orgId: string,
    sessionIds: readonly string[],
  ): Promise<readonly ShareMarkRow[]>;
}

/** A share as a closure records that it took account of it: which version, when and how widely it was shared. */
export const shareId = (
  rowId: string,
  ver: string,
  consent: { at?: unknown; scope?: unknown },
): string => {
  // As stored: a date, or its ISO text where a store keeps JSON; with everyone unless it names people.
  const at =
    consent.at instanceof Date
      ? String(consent.at.getTime())
      : typeof consent.at === 'string'
        ? consent.at
        : '';
  const scope = typeof consent.scope === 'string' ? consent.scope : 'everyone';
  return `${rowId}|${ver}|${at}|${scope}`;
};

/** When a version id was made (ms), or undefined for one that is not an ObjectId (`legacy`). */
export const madeAt = (ver: string): number | undefined =>
  /^[0-9a-f]{24}$/i.test(ver)
    ? parseInt(ver.slice(0, 8), 16) * 1000
    : undefined;

/** Whether a version made at `ver` may be one a version made at `of` was given: made before it, as clocks go. */
export const mayBeBelow = (ver: string, of: string): boolean => {
  const below = madeAt(ver);
  const above = madeAt(of);
  if (below === undefined || above === undefined) return true;
  return below <= above + CLOCK_MARGIN_MS;
};

const digestOf = (value: unknown): string =>
  createHash('sha256')
    .update(JSON.stringify(value), 'utf8')
    .digest('hex')
    .slice(0, 32);

/**
 * The stored form of `closure`, built on `base` (a version folded into it whole, stored, with fewer than
 * `CHAIN_MAX` closures behind it) when there is one; else stored whole.
 */
export function storedClosure(
  orgId: string,
  closure: Closure,
  shares: Record<string, string[]>,
  base: { stored: StoredClosure; closure: Closure } | undefined,
  at: Date,
): StoredClosure {
  const chain = base
    ? [
        { key: base.stored._id, digest: base.stored.digest },
        ...base.stored.chain,
      ]
    : [];
  const add = base
    ? closure.rest.without(base.closure.self, base.closure.rest)
    : closure.rest;
  const body = {
    _id: closureKey(closure.id, closure.ver),
    orgId,
    ...(closure.sessionId !== undefined && { sessionId: closure.sessionId }),
    rowId: closure.id,
    ver: closure.ver,
    rev: CLOSURE_REV,
    self: closure.self.toStored(),
    add: add.toStored(),
    chain,
    folded: closure.folded,
    size: closure.weight,
    spans: [...closure.spans],
    pub: closure.pub,
    shares,
  };
  return { ...body, digest: digestOf(body), at };
}

/**
 * Whether a document read from the store is a closure as this build stores them. The store holds whatever any build
 * wrote, so a store checks each document as it is read; its folds are checked when they are built (`Fold.fromStored`).
 */
export const isStoredClosure = (value: unknown): value is StoredClosure => {
  if (!isRecord(value)) return false;
  const { chain, pub, shares } = value;
  return (
    value.rev === CLOSURE_REV &&
    typeof value.rowId === 'string' &&
    typeof value.ver === 'string' &&
    (value.sessionId === undefined || typeof value.sessionId === 'string') &&
    Array.isArray(chain) &&
    (chain as unknown[]).every(
      (link) =>
        isRecord(link) &&
        typeof link.key === 'string' &&
        typeof link.digest === 'string',
    ) &&
    isStrings(value.folded) &&
    typeof value.size === 'number' &&
    isStrings(value.spans) &&
    isRecord(pub) &&
    typeof pub.self === 'boolean' &&
    typeof pub.below === 'boolean' &&
    isStrings(pub.rows) &&
    isStrings(pub.chats) &&
    isRecord(shares) &&
    Object.values(shares).every(isStrings) &&
    typeof value.digest === 'string'
  );
};

/**
 * The closure a stored one holds, from it and the closures of its chain; undefined when it is not one this build
 * reads, or a closure of its chain is missing or was made again since. Its parts are built on first use: a write reads
 * the closures of every version it was given, and builds only the one it is built on.
 */
export function closureFromStored(
  stored: StoredClosure,
  chainDocs: ReadonlyMap<string, StoredClosure>,
): Closure | undefined {
  const chain: StoredClosure[] = [];
  for (const link of stored.chain) {
    const doc = chainDocs.get(link.key);
    if (doc?.digest !== link.digest) return undefined;
    chain.push(doc);
  }
  return new KeptClosure(stored, chain);
}

/** A stored closure, its folds built when first read. */
class KeptClosure implements Closure {
  readonly id: string;
  readonly ver: string;
  readonly sessionId?: string;
  readonly folded: string[];
  readonly weight: number;
  readonly spans: ReadonlySet<string>;
  readonly pub: PubPart;
  readonly seen: Record<string, string[]>;
  private built: { self: Fold; rest: Fold } | undefined;

  constructor(
    private readonly stored: StoredClosure,
    private readonly chain: readonly StoredClosure[],
  ) {
    this.id = stored.rowId;
    this.ver = stored.ver;
    if (stored.sessionId !== undefined) this.sessionId = stored.sessionId;
    this.folded = stored.folded;
    this.weight = stored.size;
    this.spans = new Set(stored.spans);
    this.pub = stored.pub;
    this.seen = stored.shares;
  }

  get self(): Fold {
    return this.build().self;
  }

  get rest(): Fold {
    return this.build().rest;
  }

  private build(): { self: Fold; rest: Fold } {
    if (this.built) return this.built;
    // A stored form this build wrote always reads back; one it cannot read hides as a closure whose rows are gone.
    const self = Fold.fromStored(this.stored.self) ?? brokenFold();
    const rest = Fold.fromStored(this.stored.add) ?? brokenFold();
    for (const doc of this.chain) {
      rest.merge(Fold.fromStored(doc.self) ?? brokenFold());
      rest.merge(Fold.fromStored(doc.add) ?? brokenFold());
    }
    this.built = { self, rest };
    return this.built;
  }
}

const brokenFold = (): Fold => {
  const fold = new Fold();
  fold.broken = true;
  return fold;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

const isStrings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === 'string');
