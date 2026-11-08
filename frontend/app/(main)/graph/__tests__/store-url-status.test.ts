import { beforeEach, describe, expect, it } from 'vitest';
import { ErrorType, type ProcessedError } from '@/lib/api';
import { classifyGraphFailure } from '../errors';
import { emptyStateKey, noticesFor } from '../status';
import { useGraphStore } from '../store';
import { DEFAULT_URL_STATE, parseGraphEntry, parseGraphUrl, parseTrailPosition, serializeGraphUrl } from '../url-params';

const err = (statusCode?: number, type = ErrorType.UNKNOWN_ERROR): ProcessedError => ({ type, message: 'x', statusCode });

describe('classifyGraphFailure', () => {
  it('maps hidden and missing to one state, and a failed check to unavailable', () => {
    expect(classifyGraphFailure(err(404))).toBe('not-available');
    expect(classifyGraphFailure(err(503))).toBe('unavailable');
    expect(classifyGraphFailure(err(500))).toBe('unavailable');
    expect(classifyGraphFailure(err(undefined, ErrorType.NETWORK_ERROR))).toBe('unavailable');
    expect(classifyGraphFailure(err(504, ErrorType.TIMEOUT_ERROR))).toBe('unavailable');
  });

  it('names an ambiguous repo as its own state', () => {
    expect(classifyGraphFailure(err(409))).toBe('ambiguous');
  });

  it('treats a 400 as a bad cursor only while paging with one', () => {
    expect(classifyGraphFailure(err(400), true)).toBe('bad-cursor');
    expect(classifyGraphFailure(err(400), false)).toBe('unexpected');
  });

  it('leaves 401 and 403 to the app-wide handling', () => {
    expect(classifyGraphFailure(err(401))).toBe('refused');
    expect(classifyGraphFailure(err(403))).toBe('refused');
  });
});

describe('graph URL', () => {
  it('round-trips ids and filters and holds nothing else', () => {
    const state = { node: 'block:9f2c', direction: 'inbound' as const, edgeTypes: ['CALLS', 'IMPORTS_FROM'], includeTests: true };
    const params = serializeGraphUrl(state);
    expect(parseGraphUrl(params)).toEqual(state);
    expect([...params.keys()].sort()).toEqual(['dir', 'node', 'tests', 'types']);
  });

  it('falls back to the default for anything malformed instead of throwing', () => {
    const bad = new URLSearchParams('node=../../etc&dir=sideways&types=CALLS,drop table,&tests=yes');
    expect(parseGraphUrl(bad)).toEqual({ ...DEFAULT_URL_STATE, edgeTypes: ['CALLS'] });
    expect(serializeGraphUrl(DEFAULT_URL_STATE).toString()).toBe('');
  });

  it('refuses ids that are not the server\'s shape', () => {
    for (const node of ['block:', 'file:abc', 'block:a b', `block:${'x'.repeat(300)}`, '<script>']) {
      expect(parseGraphUrl(new URLSearchParams({ node })).node).toBeNull();
    }
  });
});

describe('graph entry links', () => {
  it('takes a record id, or a repo and a file with an optional symbol, and nothing else', () => {
    expect(parseGraphEntry(new URLSearchParams('record=abc-123'))).toEqual({ recordId: 'abc-123' });
    expect(parseGraphEntry(new URLSearchParams('repo=acme/shop&file=src/a.py&symbol=function:main'))).toEqual({
      repo: 'acme/shop',
      filePath: 'src/a.py',
      qualifiedName: 'function:main',
    });
    expect(parseGraphEntry(new URLSearchParams('repo=acme/shop'))).toBeNull();
    expect(parseGraphEntry(new URLSearchParams(''))).toBeNull();
  });

  it('refuses a malformed record id and oversized or control-character text', () => {
    expect(parseGraphEntry(new URLSearchParams('record=../x'))).toBeNull();
    expect(parseGraphEntry(new URLSearchParams({ repo: 'a'.repeat(301), file: 'x' }))).toBeNull();
    expect(parseGraphEntry(new URLSearchParams({ repo: 'a', file: 'x\u0000y' }))).toBeNull();
  });

  it('is never written back: the rewritten URL carries ids only', () => {
    const params = serializeGraphUrl({ ...DEFAULT_URL_STATE, node: 'block:found' });
    expect([...params.keys()]).toEqual(['node']);
  });
});

describe('graph store', () => {
  beforeEach(() => useGraphStore.getState().reset());

  it('keeps a trail with back and forward, and a new focus drops the forward entries', () => {
    const s = () => useGraphStore.getState();
    s().focusOn('block:a');
    s().focusOn('block:b');
    s().focusOn('block:c');
    s().back();
    expect(s().node).toBe('block:b');
    s().forward();
    expect(s().node).toBe('block:c');
    s().back();
    s().back();
    s().focusOn('block:d');
    expect(s().trail).toEqual(['block:a', 'block:d']);
    expect(s().node).toBe('block:d');
  });

  it('forgets revoked ids from the selection, the trail and the focus, leaving no trace', () => {
    const s = () => useGraphStore.getState();
    s().focusOn('block:a');
    s().focusOn('block:secret');
    s().focusOn('block:c');
    s().select('block:secret');
    s().forget(['block:secret']);
    expect(s().trail).toEqual(['block:a', 'block:c']);
    expect(s().selection).toBeNull();
    expect(JSON.stringify(s().trail)).not.toContain('secret');
    s().forget(['block:c']);
    expect(s().node).toBe('block:a');
    s().forget(['block:a']);
    expect(s().node).toBeNull();
    expect(s().trail).toEqual([]);
  });

  it('steps quality down to the last rung and no further, and resets with the session', () => {
    const s = () => useGraphStore.getState();
    for (let i = 0; i < 9; i++) s().stepDown();
    expect(s().qualityStep).toBe(5);
    s().reset();
    expect(s().qualityStep).toBe(0);
  });
});

describe('notices', () => {
  const base = { status: 'ready' as const, incomplete: false, verify: 'ok' as const, failure: null, simplified: false };

  it('says nothing when everything is fine', () => {
    expect(noticesFor(base)).toEqual([]);
  });

  it('a failed check is an error and never an empty graph', () => {
    expect(noticesFor({ ...base, failure: 'unavailable' })).toEqual([{ level: 'error', key: 'graph.error.unavailable', action: 'retry' }]);
  });

  it('puts access verification first, then the failure, the build, incompleteness and simplification', () => {
    const keys = noticesFor({ status: 'stale', incomplete: true, verify: 'degraded', failure: 'unavailable', simplified: true }).map((n) => n.key);
    expect(keys).toEqual(['graph.verify.degraded', 'graph.error.unavailable', 'graph.status.stale', 'graph.incomplete', 'graph.simplified']);
  });

  it('tells the person which repository to choose without repeating the server (no API wording)', () => {
    expect(noticesFor({ ...base, failure: 'ambiguous' })).toEqual([{ level: 'info', key: 'graph.error.ambiguous' }]);
  });

  it('offers Retry on a failed read, and full quality on a simplified or table-only view', () => {
    expect(noticesFor({ ...base, failure: 'unexpected' })[0]).toMatchObject({ action: 'retry' });
    expect(noticesFor({ ...base, simplified: true })).toEqual([{ level: 'info', key: 'graph.simplified', action: 'restore' }]);
    expect(noticesFor({ ...base, simplified: true, tableOnly: true })).toEqual([{ level: 'info', key: 'graph.tableOnly', action: 'restore' }]);
  });

  it('says tests are off next to "not available", and only then, and says when an expand added nothing', () => {
    expect(noticesFor({ ...base, failure: 'not-available', includeTests: false }).map((n) => n.key)).toEqual(['graph.error.notAvailable', 'graph.hint.testsOff']);
    expect(noticesFor({ ...base, failure: 'not-available', includeTests: true }).map((n) => n.key)).toEqual(['graph.error.notAvailable']);
    expect(noticesFor({ ...base, lastAdded: 0 }).map((n) => n.key)).toEqual(['graph.nothingNew']);
    expect(noticesFor({ ...base, lastAdded: 3 })).toEqual([]);
    expect(noticesFor({ ...base, lastAdded: 0, failure: 'unavailable' }).map((n) => n.key)).not.toContain('graph.nothingNew');
  });

  it('says the focus went when no view is left', () => {
    expect(noticesFor({ ...base, failure: 'revoked' })).toEqual([{ level: 'info', key: 'graph.error.revoked' }]);
  });

  it('gives a repository that cannot be graphed an empty state, not a banner', () => {
    expect(emptyStateKey('never_built')).toBe('graph.empty.neverBuilt');
    expect(emptyStateKey('too_large')).toBe('graph.empty.tooLarge');
    expect(emptyStateKey('ready')).toBeNull();
    expect(noticesFor({ ...base, status: 'never_built' })).toEqual([]);
  });

  it('never mentions a count, a name or an id', () => {
    const everything = (['never_built', 'building', 'stale', 'failed', 'too_large', 'unknown'] as const).flatMap((status) =>
      noticesFor({ status, incomplete: true, verify: 'blank', failure: 'not-available', simplified: true }),
    );
    for (const n of everything) expect(n.key).toMatch(/^graph\.[a-zA-Z.]+$/);
  });
});

describe('repo scope link', () => {
  it('a repo with no file scopes the search; with a file it is an entry instead', async () => {
    const { parseRepoScope } = await import('../url-params');
    expect(parseRepoScope(new URLSearchParams('repo=acme/shop'))).toBe('acme/shop');
    expect(parseRepoScope(new URLSearchParams('repo=acme/shop&file=a.py'))).toBeNull();
    expect(parseRepoScope(new URLSearchParams(`repo=${'a'.repeat(301)}`))).toBeNull();
    expect(parseRepoScope(new URLSearchParams(''))).toBeNull();
  });
});

describe('crowding notices', () => {
  const base = { status: 'ready' as const, incomplete: false, verify: 'ok' as const, failure: null, simplified: false };

  it('says crowded past the soft cap, and capped (not both) past the hard cap, and nothing under it', () => {
    const caps = { softCap: 300, hardCap: 1000 };
    expect(noticesFor({ ...base, ...caps, nodeCount: 300 })).toEqual([]);
    expect(noticesFor({ ...base, ...caps, nodeCount: 301 }).map((n) => n.key)).toEqual(['graph.crowded']);
    expect(noticesFor({ ...base, ...caps, nodeCount: 1001 }).map((n) => n.key)).toEqual(['graph.capped']);
  });

  it('says the view is full, as a warning', () => {
    expect(noticesFor({ ...base, failure: 'full' })).toEqual([{ level: 'warning', key: 'graph.error.full' }]);
  });
});


describe('the trail position on a history entry', () => {
  it('is written only beside a node, and read back only when it is a small whole number', () => {
    expect(serializeGraphUrl({ ...DEFAULT_URL_STATE, node: 'block:a' }, 2).toString()).toBe('node=block%3Aa&at=2');
    expect(serializeGraphUrl(DEFAULT_URL_STATE, 2).toString()).toBe('');
    expect(parseTrailPosition(new URLSearchParams('node=block%3Aa&at=2'))).toBe(2);
    for (const bad of ['', '-1', '1.5', 'x', '1000', '1e2', ' 2']) {
      expect(parseTrailPosition(new URLSearchParams({ node: 'block:a', at: bad })), bad).toBeUndefined();
    }
    expect(parseTrailPosition(new URLSearchParams('node=block%3Aa'))).toBeUndefined();
  });
});


describe('forget moves the view forward when the first entry goes and others remain', () => {
  it('keeps a focus on the nearest entry after it instead of clearing the view', () => {
    const store = useGraphStore;
    store.getState().reset();
    for (const id of ['block:a', 'block:b']) store.getState().focusOn(id);
    store.getState().jumpTo(0); // Back from [a, b]
    store.getState().forget(['block:a']);
    const s = store.getState();
    expect(s.trail).toEqual(['block:b']);
    expect(s.trailIndex).toBe(0);
    expect(s.node).toBe('block:b');
  });

  it('still empties the view when nothing remains', () => {
    const store = useGraphStore;
    store.getState().reset();
    store.getState().focusOn('block:a');
    store.getState().forget(['block:a']);
    expect(store.getState().node).toBeNull();
    expect(store.getState().trailIndex).toBe(-1);
  });
});
