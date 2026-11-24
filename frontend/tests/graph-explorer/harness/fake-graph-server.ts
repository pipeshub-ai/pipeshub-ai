// An in-browser stand-in for the graph and code-graph APIs over a seeded synthetic code graph.
// It follows the real contract: 20 anchors and 100 neighbours at most, an opaque offset cursor,
// totals that are only a lower bound when truncated, one 404 for hidden and missing, 503 on an outage.

const FILES = 80;
const PER_FILE = 25;
const HUBS = 12;

function mulberry32(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface Block { id: string; n: number; file: number; kind: string; label: string; line: number }
interface Edge { id: string; from: string; to: string; type: string; confidence: string; line: number }

const rand = mulberry32(42);
export const blocks: Block[] = Array.from({ length: FILES * PER_FILE }, (_, n) => ({
  id: `block:b${String(n).padStart(4, '0')}`,
  n,
  file: Math.floor(n / PER_FILE),
  kind: n % 11 === 0 ? 'class' : 'function',
  label: `${n % 11 === 0 ? 'Class' : 'fn'}_${n}`,
  line: 1 + (n % PER_FILE) * 12,
}));
const edges: Edge[] = [];
const out = new Map<string, Edge[]>();
const into = new Map<string, Edge[]>();
const seen = new Set<string>();
for (const b of blocks) {
  const calls = 2 + Math.floor(rand() * 4);
  for (let i = 0; i < calls; i++) {
    const target = blocks[rand() < 0.35 ? Math.floor(rand() * HUBS) : Math.floor(rand() * blocks.length)];
    if (target.id === b.id) continue;
    const id = `e:${b.id}>${target.id}:CALLS`;
    if (seen.has(id)) continue;
    seen.add(id);
    const e: Edge = { id, from: b.id, to: target.id, type: 'CALLS', confidence: rand() < 0.2 ? 'INFERRED' : 'EXTRACTED', line: b.line + 1 + i };
    edges.push(e);
    (out.get(b.id) ?? out.set(b.id, []).get(b.id)!).push(e);
    (into.get(target.id) ?? into.set(target.id, []).get(target.id)!).push(e);
  }
}
const byId = new Map(blocks.map((b) => [b.id, b]));

export const isTestFile = (file: number) => file % 9 === 4;
const hiddenFromBob = (file: number) => file % 5 === 3;

// A test can set the starting state before any script runs, with `window.__fakeInit`.
const init = ((window as unknown as { __fakeInit?: Record<string, unknown> }).__fakeInit ?? {}) as Partial<{ persona: 'owner' | 'bob'; latencyMs: number; outage: boolean; dense: boolean; status: string }>;

export const fake = {
  persona: (init.persona ?? 'owner') as 'owner' | 'bob',
  revoked: new Set<number>(),
  outage: init.outage ?? false,
  /** What the explorer is told about the graph build. */
  status: init.status ?? 'ready',
  /** Pretend the scan budget ran out. */
  incomplete: false,
  /** Every expand returns `limit` brand-new neighbours per anchor, to grow a view past the node caps quickly. */
  dense: init.dense ?? false,
  latencyMs: init.latencyMs ?? 0,
  /** The most-called blocks: good places to start. */
  hubs: [...blocks.slice(0, HUBS)].map((b) => b.id),
  fileOf: (id: string) => byId.get(id)?.file,
  /** What a person who may read everything the current persona may would see: the oracle for paging tests. */
  visibleNeighbours: (id: string, direction = 'any', includeTests = false) => {
    const mine = [...(direction !== 'inbound' ? out.get(id) ?? [] : []), ...(direction !== 'outbound' ? into.get(id) ?? [] : [])];
    const far = new Set(mine.map((e) => (e.from === id ? e.to : e.from)).filter((x) => canSee(byId.get(x), includeTests)));
    return [...far];
  },
  canSeeId: (id: string, includeTests = false) => canSee(byId.get(id), includeTests),
  /** The block ids of one file, for revoking a node's file. */
  idsInFile: (file: number) => blocks.filter((b) => b.file === file).map((b) => b.id),
};

const canSee = (b: Block | undefined, includeTests: boolean) =>
  !!b && !fake.revoked.has(b.file) && (fake.persona === 'owner' || !hiddenFromBob(b.file)) && (includeTests || !isTestFile(b.file));

const node = (b: Block) => ({ id: b.id, kind: b.kind, label: b.label, recordId: `rec-${b.file}`, file: `src/mod_${b.file}/file_${b.file}.py`, line: b.line });
const wire = (e: Edge) => ({ id: e.id, from: e.from, to: e.to, type: e.type, confidence: e.confidence, line: e.line });

function fail(statusCode: number, message: string): never {
  // The same shape the product's API client rejects with (ProcessedError).
  throw { type: statusCode === 404 ? 'NOT_FOUND' : 'SERVER_ERROR', message, statusCode };
}

const encode = (offset: number) => btoa(`v1:${offset}`).replace(/=+$/, '');
function decode(cursor?: string): number {
  if (cursor === undefined) return 0;
  try {
    const [v, n] = atob(cursor).split(':');
    if (v === 'v1' && /^\d{1,4}$/.test(n)) return Number(n);
  } catch { /* falls through */ }
  return fail(400, 'Invalid cursor.');
}

interface ExpandBody { nodeIds: string[]; direction?: string; edgeTypes?: string[]; includeTests?: boolean; limit?: number; cursor?: string }

function expand(body: ExpandBody) {
  const tests = !!body.includeTests;
  const limit = Math.min(100, Math.max(1, body.limit ?? 50));
  const offset = decode(body.cursor);
  if (!Array.isArray(body.nodeIds) || body.nodeIds.length < 1 || body.nodeIds.length > 20) fail(400, 'Bad request.');
  const anchors = [...new Set(body.nodeIds)].filter((id) => canSee(byId.get(id), tests));
  if (anchors.length === 0) fail(404, 'No readable node matches the request.');
  const dir = body.direction ?? 'any';
  if (fake.dense) {
    const made: Edge[] = [];
    for (const a of anchors) {
      for (let i = 0; i < limit; i++) {
        const id = `${a}~${i}`;
        if (!byId.has(id)) byId.set(id, { id, n: -1, file: byId.get(a)!.file, kind: 'function', label: `syn_${a.slice(7)}_${i}`, line: 1 });
        made.push({ id: `e:${a}>${id}:CALLS`, from: a, to: id, type: 'CALLS', confidence: 'EXTRACTED', line: 1 });
      }
    }
    const ends = new Set([...anchors, ...made.flatMap((e) => [e.from, e.to])]);
    return {
      nodes: [...ends].map((id) => node(byId.get(id)!)),
      edges: made.map(wire),
      anchors: anchors.map((id) => ({ id, total: limit })),
      page: { truncated: false, incomplete: false },
      graphStatus: fake.status,
    };
  }
  const rows = new Map<string, Edge>();
  for (const a of anchors) {
    const mine = [...(dir !== 'inbound' ? out.get(a) ?? [] : []), ...(dir !== 'outbound' ? into.get(a) ?? [] : [])];
    for (const e of mine) {
      const far = byId.get(e.from === a ? e.to : e.from);
      if (canSee(far, tests) && (!body.edgeTypes?.length || body.edgeTypes.includes(e.type))) rows.set(e.id, e);
    }
  }
  const all = [...rows.values()].sort((x, y) => x.id.localeCompare(y.id));
  const truncated = all.length > offset + limit;
  const page = all.slice(offset, offset + limit);
  const ends = new Set([...anchors, ...page.flatMap((e) => [e.from, e.to])]);
  return {
    nodes: [...ends].map((id) => node(byId.get(id)!)),
    edges: page.map(wire),
    anchors: anchors.map((id) => ({ id, total: Math.min(all.filter((e) => e.from === id || e.to === id).length, offset + limit) })),
    page: { truncated, incomplete: fake.incomplete, ...(truncated ? { nextCursor: encode(offset + page.length) } : {}) },
    graphStatus: fake.status,
  };
}

const REPOS = [
  { repo: 'acme/shop', source: 'GitHub', connector_id: 'c1', graph_status: 'ready' },
  { repo: 'acme/admin', source: 'GitHub', connector_id: 'c1', graph_status: 'stale' },
];
const fileNumber = (path: string) => Number(/^src\/mod_(\d+)\/file_\1\.py$/.exec(path)?.[1] ?? NaN);

function search(body: { repo: string; pattern: string; include_tests?: boolean; limit?: number }) {
  // Everything lives in acme/shop; other repos are empty. Identifier search: a name contains the pattern.
  if (body.repo !== 'acme/shop') return { matches: [] };
  const wanted = body.pattern.toLowerCase();
  const matches = blocks
    .filter((b) => b.label.toLowerCase().includes(wanted) && canSee(b, !!body.include_tests))
    .slice(0, Math.min(50, body.limit ?? 20))
    .map((b) => ({ file_path: `src/mod_${b.file}/file_${b.file}.py`, qualified_name: `${b.kind}:${b.label}`, kind: b.kind, repo: 'acme/shop', start_line: b.line }));
  return { matches };
}

function resolve(body: { recordId?: string; repo?: string; filePath?: string; qualifiedName?: string; includeTests?: boolean }) {
  if (body.repo === 'shop') fail(409, "'shop' matches 2 repositories: GitHub:acme/shop, GitLab:acme/shop. Call again with `repo` set to one of them.");
  const file = body.recordId ? Number(/^rec-(\d+)$/.exec(body.recordId)?.[1] ?? NaN) : body.repo === 'acme/shop' ? fileNumber(body.filePath ?? '') : NaN;
  const wanted = body.qualifiedName;
  const found = blocks.find((b) => b.file === file && (!wanted || `${b.kind}:${b.label}` === wanted));
  if (!found || !canSee(found, !!body.includeTests)) fail(404, 'No readable node matches the request.');
  return { nodeId: found.id };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export async function handle(url: string, body: unknown): Promise<unknown> {
  await sleep(fake.latencyMs);
  if (fake.outage) fail(503, 'Graph unavailable.');
  if (url.endsWith('/expand')) return expand(body as ExpandBody);
  if (url.endsWith('/repos')) return { repos: REPOS };
  if (url.endsWith('/search')) return search(body as Parameters<typeof search>[0]);
  if (url.endsWith('/resolve')) return resolve(body as Parameters<typeof resolve>[0]);
  if (url.endsWith('/revalidate')) {
    const { nodeIds, includeTests } = body as { nodeIds: string[]; includeTests?: boolean };
    return { visible: nodeIds.filter((id) => canSee(byId.get(id), !!includeTests)) };
  }
  return fail(404, 'Not found.');
}

// Tests steer the fake through this: persona, revoked files, outage, latency, graph status, incomplete.
declare global {
  interface Window {
    __fake: typeof fake;
  }
}
window.__fake = fake;
