// Regression tests for bugs found in review: each one fails if its fix is removed.
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Core } from 'cytoscape';
import { ErrorType, type ProcessedError } from '@/lib/api';
import { ExplorerController, type ExplorerApi } from '../controller';
import { createCytoscapeEngine } from '../engine/cytoscape-engine';
import { Revalidator } from '../engine/revalidate';
import { qualityFor } from '../engine/tiers';
import { applyVisible, emptyModel, mergeExpand, pruneOrphans } from '../model/graph-model';
import { placeNodes, rowsEitherSide, CELL_HEIGHT, CELL_WIDTH } from '../model/placement';
import { useGraphStore } from '../store';
import type { ExpandRequest, ExpandResponse } from '../types';

const answer = (anchor: string, others: string[], over: Partial<ExpandResponse> = {}): ExpandResponse => ({
  nodes: [anchor, ...others].map((id) => ({ id, label: id })),
  edges: others.map((id) => ({ id: `e:${anchor}>${id}`, from: anchor, to: id, type: 'CALLS' })),
  anchors: [{ id: anchor, total: others.length }],
  page: { truncated: false, incomplete: false },
  graphStatus: 'ready',
  ...over,
});
const fail = (statusCode: number): ProcessedError => ({ type: ErrorType.UNKNOWN_ERROR, message: 'x', statusCode });

/** A world where some ids can be made to answer 404 or 503. */
function world() {
  const state = { missing: new Set<string>(), down: false, calls: [] as ExpandRequest[], gate: new Map<string, Promise<void>>() };
  const api = {
    expand: vi.fn(async (req: ExpandRequest) => {
      state.calls.push(req);
      await state.gate.get(req.nodeIds[0]);
      if (state.down) throw fail(503);
      if (req.nodeIds.some((id) => state.missing.has(id))) throw fail(404);
      return answer(req.nodeIds[0], [`${req.nodeIds[0]}.a`, `${req.nodeIds[0]}.b`]);
    }),
    resolve: vi.fn(),
    revalidate: vi.fn(),
  } as unknown as ExplorerApi & { expand: ReturnType<typeof vi.fn> };
  return { state, api, controller: new ExplorerController(api) };
}
const ids = (c: ExplorerController) => Object.keys(c.getSnapshot().model.nodes).sort();

beforeEach(() => useGraphStore.getState().reset());

describe('a failed Open, Back, Forward or filter change never blanks the view', () => {
  it('keeps the old view, the trail and the focus when the new one cannot be opened', async () => {
    const { controller, state } = world();
    await controller.open('block:a');
    state.down = true;
    expect(await controller.open('block:b')).toBe(false);
    expect(ids(controller)).toEqual(['block:a', 'block:a.a', 'block:a.b']);
    expect(useGraphStore.getState().trail).toEqual(['block:a']);
    expect(useGraphStore.getState().node).toBe('block:a');
    expect(controller.getSnapshot().failure).toBe('unavailable');
    state.down = false;
    state.missing.add('block:c');
    expect(await controller.open('block:c')).toBe(false);
    expect(controller.getSnapshot().failure).toBe('not-available');
    expect(ids(controller)).toContain('block:a');
    expect(useGraphStore.getState().node).toBe('block:a');
  });

  it('puts a filter back when re-opening under it fails, so the controls match what is drawn', async () => {
    const { controller, state } = world();
    await controller.open('block:a');
    state.down = true;
    expect(await controller.applyFilters({ direction: 'inbound', includeTests: true })).toBe(false);
    expect(useGraphStore.getState()).toMatchObject({ direction: 'any', includeTests: false });
    state.down = false;
    expect(await controller.applyFilters({ direction: 'outbound' })).toBe(true);
    expect(useGraphStore.getState().direction).toBe('outbound');
  });

  it('Back skips a node that is no longer available and lands on the next one', async () => {
    const { controller, state } = world();
    for (const id of ['block:a', 'block:b', 'block:c']) await controller.open(id);
    state.missing.add('block:b');
    expect(await controller.go(-1)).toBe(true);
    expect(useGraphStore.getState().node).toBe('block:a');
    expect(useGraphStore.getState().trail).toEqual(['block:a', 'block:c']);
    expect(ids(controller)).toContain('block:a');
  });

  it('Back during an outage stays where it was and says so', async () => {
    const { controller, state } = world();
    await controller.open('block:a');
    await controller.open('block:b');
    state.down = true;
    expect(await controller.go(-1)).toBe(false);
    expect(useGraphStore.getState()).toMatchObject({ node: 'block:b', trailIndex: 1 });
    expect(controller.getSnapshot().failure).toBe('unavailable');
    expect(ids(controller)).toContain('block:b');
  });

  it('Back and Forward at the ends do nothing', async () => {
    const { controller } = world();
    await controller.open('block:a');
    expect(await controller.go(-1)).toBe(false);
    expect(await controller.go(1)).toBe(false);
  });

  it('Forward moves along the trail and Back returns', async () => {
    const { controller } = world();
    await controller.open('block:a');
    await controller.open('block:b');
    expect(await controller.go(-1)).toBe(true);
    expect(useGraphStore.getState().node).toBe('block:a');
    expect(await controller.go(1)).toBe(true);
    expect(useGraphStore.getState().node).toBe('block:b');
  });
});

describe('Retry repeats the last call', () => {
  it('repeats a failed open once the service is back, and clears the failure', async () => {
    const { controller, state } = world();
    state.down = true;
    await controller.open('block:a');
    expect(controller.getSnapshot().failure).toBe('unavailable');
    state.down = false;
    await controller.retry();
    expect(controller.getSnapshot().failure).toBeNull();
    expect(useGraphStore.getState().node).toBe('block:a');
  });

  it('does nothing when there was no call', async () => {
    const { controller, api } = world();
    await controller.retry();
    expect(api.expand).not.toHaveBeenCalled();
  });
});

describe('an expand that adds nothing says so', () => {
  it('reports how many nodes an expand added, and none for an open', async () => {
    const { controller } = world();
    await controller.open('block:a');
    expect(controller.getSnapshot().lastAdded).toBeNull();
    await controller.expand(['block:a']); // the same two neighbours again
    expect(controller.getSnapshot().lastAdded).toBe(0);
    await controller.expand(['block:a.a']);
    expect(controller.getSnapshot().lastAdded).toBe(2);
  });
});

describe('revocation leaves no holes, orphans or silence', () => {
  const asked = (c: ExplorerController) => c.ids();

  it('removes nodes that only a revoked node linked, and keeps the focus', async () => {
    const { controller } = world();
    await controller.open('block:a');
    await controller.expand(['block:a.a']); // a.a.a, a.a.b hang off a.a
    controller.revoke(asked(controller), asked(controller).filter((id) => id !== 'block:a.a'));
    expect(ids(controller)).toEqual(['block:a', 'block:a.b']);
    expect(JSON.stringify(controller.getSnapshot().model)).not.toContain('a.a');
  });

  it('opens the nearest trail entry that is left when the focus goes', async () => {
    const { controller, api } = world();
    await controller.open('block:a');
    await controller.open('block:b');
    controller.revoke(asked(controller), asked(controller).filter((id) => id !== 'block:b'));
    await vi.waitFor(() => expect(useGraphStore.getState().node).toBe('block:a'));
    await vi.waitFor(() => expect(ids(controller)).toContain('block:a.a'));
    expect(JSON.stringify(controller.getSnapshot().model)).not.toContain('block:b');
    expect(api.expand).toHaveBeenCalledTimes(3);
  });

  it('says the view is gone when nothing on the trail is left', async () => {
    const { controller } = world();
    await controller.open('block:a');
    controller.revoke(asked(controller), []);
    await vi.waitFor(() => expect(controller.getSnapshot().failure).toBe('revoked'));
    expect(ids(controller)).toEqual([]);
    expect(useGraphStore.getState().node).toBeNull();
  });

  it('skips a trail entry that has also gone', async () => {
    const { controller, state } = world();
    for (const id of ['block:a', 'block:b', 'block:c']) await controller.open(id);
    state.missing.add('block:b');
    controller.revoke(asked(controller), asked(controller).filter((id) => id !== 'block:c'));
    await vi.waitFor(() => expect(useGraphStore.getState().node).toBe('block:a'));
  });

  it('pruneOrphans and applyVisible keep what the caller says to keep', () => {
    const model = mergeExpand(emptyModel(), { source: 'code', nodeIds: ['x'] }, answer('x', ['y']));
    expect(Object.keys(pruneOrphans(model, ['x']).nodes)).toEqual(['x', 'y']);
    const lone = mergeExpand(emptyModel(), { source: 'code', nodeIds: ['x'] }, { ...answer('x', []), edges: [] });
    expect(Object.keys(pruneOrphans(lone).nodes)).toEqual([]);
    expect(Object.keys(pruneOrphans(lone, ['x']).nodes)).toEqual(['x']);
    const out = applyVisible(model, ['y'], [], ['x']);
    expect(Object.keys(out.model.nodes)).toEqual(['x']);
    expect(out.revoked).toEqual(['y']);
    expect(out.pruned).toEqual([]);
    expect(applyVisible(model, ['y'], ['y'], ['x']).model).toBe(model);
  });
});

describe('a big group makes a block as wide as it is tall', () => {
  it('uses taller columns as the group grows', () => {
    expect(rowsEitherSide(1)).toBe(3);
    expect(rowsEitherSide(50)).toBeGreaterThanOrEqual(6);
    expect(rowsEitherSide(172)).toBeGreaterThan(rowsEitherSide(50));
  });

  it('lays 172 neighbours out close to square, not as a thin strip', () => {
    const placed = Object.values(placeNodes(new Set(), { c: 0, r: 0 }, 1, Array.from({ length: 172 }, (_, i) => `n${i}`)));
    const width = (Math.max(...placed.map((c) => c.c)) - Math.min(...placed.map((c) => c.c)) + 1) * CELL_WIDTH;
    const height = (Math.max(...placed.map((c) => c.r)) - Math.min(...placed.map((c) => c.r)) + 1) * CELL_HEIGHT;
    expect(width / height).toBeGreaterThan(0.6);
    expect(width / height).toBeLessThan(1.8);
  });
});

describe('after the picture was cleared, a working check is what brings it back', () => {
  it('probes with the last ids while blank, and says ok only when a probe succeeds', async () => {
    let now = 0;
    const timers: { fn: () => void; at: number }[] = [];
    let up = true;
    let shown: string[] = ['block:a', 'block:b'];
    const states: string[] = [];
    const call = vi.fn(async (asked: string[]) => (up ? asked : Promise.reject(new Error('down'))));
    const r = new Revalidator({
      getIds: () => shown,
      call,
      onResult: () => undefined,
      onState: (s) => states.push(s),
      now: () => now,
      random: () => 0,
      setTimer: (fn, ms) => (timers.push({ fn, at: now + ms }), timers.length),
      clearTimer: () => undefined,
    });
    const tick = async () => {
      timers.sort((a, b) => a.at - b.at);
      const t = timers.shift()!;
      now = t.at;
      t.fn();
      await new Promise((resolve) => setTimeout(resolve, 0));
    };
    r.start();
    await tick(); // ok, remembers the ids
    up = false;
    while (now < 6 * 60_000) await tick();
    expect(states.at(-1)).toBe('blank');
    shown = []; // the picture was cleared
    await tick();
    expect(states.at(-1)).toBe('blank'); // nothing to ask about is not a working check
    expect(call.mock.calls.at(-1)?.[0]).toEqual(['block:a', 'block:b']);
    up = true;
    await tick();
    expect(states.at(-1)).toBe('ok');
  });
});

describe('restoreFocus brings the view back', () => {
  it('re-opens the focus after the picture was cleared', async () => {
    const { controller } = world();
    await controller.open('block:a');
    controller.clear();
    expect(ids(controller)).toEqual([]);
    await controller.restoreFocus();
    expect(ids(controller)).toContain('block:a');
  });
});

describe('the ring, the focus mark and a stale click position survive a redraw', () => {
  it('keeps the selection ring and the focus mark when the model changes, and re-reads the container on resize', async () => {
    let cy!: Core;
    const engine = createCytoscapeEngine({ headless: true, expose: (c) => (cy = c) });
    await engine.mount(document.createElement('div'), qualityFor({ hasWebgl2: false, renderer: '', devicePixelRatio: 1, reducedMotion: false }, 'canvas', 0), {
      onSelect: () => undefined, onExpand: () => undefined, onHover: () => undefined, onFrame: () => undefined,
    });
    engine.select('block:a');
    engine.setFocus('block:a');
    const first = mergeExpand(emptyModel(), { source: 'code', nodeIds: ['block:a'] }, answer('block:a', ['block:b']));
    engine.setModel(first);
    expect(cy.getElementById('block:a').selected()).toBe(true);
    expect(cy.getElementById('block:a').hasClass('focus')).toBe(true);
    const second = mergeExpand(first, { source: 'code', nodeIds: ['block:a'] }, answer('block:a', ['block:c']));
    engine.setModel(second);
    expect(cy.getElementById('block:a').selected()).toBe(true);
    engine.setFocus('block:b');
    expect(cy.getElementById('block:a').hasClass('focus')).toBe(false);
    expect(cy.getElementById('block:b').hasClass('focus')).toBe(true);
    const spy = vi.spyOn(cy, 'resize');
    engine.resize();
    expect(spy).toHaveBeenCalled();
    engine.destroy();
  });
});

describe('an answer that was already on its way cannot put a revoked node back', () => {
  it('strips a node removed by a re-check, and what only it linked, from a late expand answer', async () => {
    let release!: (r: ExpandResponse) => void;
    const late = new Promise<ExpandResponse>((resolve) => (release = resolve));
    let calls = 0;
    const api = {
      expand: vi.fn(async (req: ExpandRequest) => (++calls === 2 ? late : answer(req.nodeIds[0], ['block:b']))),
      resolve: vi.fn(),
      revalidate: vi.fn(),
    } as unknown as ExplorerApi;
    const controller = new ExplorerController(api);
    await controller.open('block:a');
    const pending = controller.expand(['block:b']);
    controller.revoke(controller.ids(), ['block:a']); // b is revoked while its expand is in flight
    release(answer('block:b', ['block:c']));
    await pending;
    expect(JSON.stringify(controller.getSnapshot().model)).not.toContain('block:b');
    expect(JSON.stringify(controller.getSnapshot().model)).not.toContain('block:c');
    expect(ids(controller)).toEqual(['block:a']);
  });

  it('forgets what was revoked when a new view starts, so a node that is allowed again can appear', async () => {
    const { controller, state } = world();
    await controller.open('block:a');
    state.missing.add('block:a');
    controller.revoke(controller.ids(), []);
    await vi.waitFor(() => expect(controller.getSnapshot().failure).toBe('revoked'));
    await controller.open('block:a.a');
    expect(ids(controller)).toContain('block:a.a');
  });
});

describe('a second click for the same page waits for the first', () => {
  it('sends one request for ten quick expands of one node and one for ten Show mores', async () => {
    const { controller, api } = world();
    await controller.open('block:a');
    api.expand.mockClear();
    await Promise.all(Array.from({ length: 10 }, () => controller.expand(['block:a.a'])));
    expect(api.expand).toHaveBeenCalledTimes(1);
    expect(ids(controller)).toContain('block:a.a.a');
  });

  it('asks again once the first answer is in', async () => {
    const { controller, api } = world();
    await controller.open('block:a');
    api.expand.mockClear();
    await controller.expand(['block:a.a']);
    await controller.expand(['block:a.a']);
    expect(api.expand).toHaveBeenCalledTimes(2);
  });
});

describe('a check that never answers is given up on', () => {
  it('aborts it after the timeout, counts it as a failure, and still reaches "blank"', async () => {
    let now = 0;
    const timers: { fn: () => void; at: number; id: number }[] = [];
    let nextId = 1;
    const aborted: boolean[] = [];
    const states: string[] = [];
    const r = new Revalidator({
      getIds: () => ['block:a'],
      call: (_ids, signal) => new Promise<string[]>(() => signal?.addEventListener('abort', () => aborted.push(true))),
      onResult: () => undefined,
      onState: (s) => states.push(s),
      now: () => now,
      random: () => 0,
      setTimer: (fn, ms) => {
        const t = { fn, at: now + ms, id: nextId++ };
        timers.push(t);
        return t.id;
      },
      clearTimer: (h) => {
        const i = timers.findIndex((t) => t.id === h);
        if (i >= 0) timers.splice(i, 1);
      },
    });
    r.start();
    while (now < 7 * 60_000 && timers.length) {
      timers.sort((a, b) => a.at - b.at);
      const t = timers.shift()!;
      now = t.at;
      t.fn();
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    expect(aborted.length).toBeGreaterThan(5);
    expect(states).toContain('degraded');
    expect(states.at(-1)).toBe('blank');
  });
});

describe('the canvas colours are visible against a white page', () => {
  const lum = (hex: string) => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const contrast = (a: string, b: string) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);

  it('draws edges, the selection ring and every node colour at 3:1 or better, and classes as squares', async () => {
    const { buildStyle } = await import('../engine/cytoscape-engine');
    const style = JSON.stringify(buildStyle());
    for (const colour of style.match(/#[0-9a-f]{6}/gi) ?? []) {
      if (['#ffffff', '#1c2220'].includes(colour.toLowerCase())) continue;
      if (colour.toLowerCase() === '#4f6fd0' || colour.toLowerCase() === '#3f8f6b') {
        expect(contrast(colour, '#ffffff'), colour).toBeGreaterThan(3);
        continue;
      }
      expect(contrast(colour, '#ffffff'), colour).toBeGreaterThanOrEqual(3);
    }
    expect(style).toContain('round-rectangle');
  });
});

describe('review finding again (found by the browser suite): blank holds when the service was down from the start', () => {
  it('keeps asking about the ids it was given even though no check ever succeeded, and stays blank', async () => {
    let now = 0;
    const timers: { fn: () => void; at: number }[] = [];
    let shown = ['block:a'];
    const states: string[] = [];
    const call = vi.fn(async () => Promise.reject(new Error('down')));
    const r = new Revalidator({
      getIds: () => shown,
      call,
      onResult: () => undefined,
      onState: (s) => states.push(s),
      now: () => now,
      random: () => 0,
      setTimer: (fn, ms) => (timers.push({ fn, at: now + ms }), timers.length),
      clearTimer: () => undefined,
    });
    const tick = async () => {
      timers.sort((a, b) => a.at - b.at);
      const t = timers.shift()!;
      now = t.at;
      t.fn();
      await new Promise((resolve) => setTimeout(resolve, 0));
    };
    r.start();
    while (now < 6 * 60_000) await tick();
    expect(states.at(-1)).toBe('blank');
    shown = []; // the picture was cleared
    for (let i = 0; i < 6; i++) await tick();
    expect(states.at(-1)).toBe('blank');
    expect(call.mock.calls.at(-1)?.[0]).toEqual(['block:a']);
  });
});

describe('only what the check took away is forgotten, not what merely lost its link', () => {
  it('keeps a readable node that was linked only through a revoked focus on the trail, and returns to it', async () => {
    const api = {
      // f's view shows r and g; every other node has no neighbours
      expand: vi.fn(async (req: ExpandRequest) => (req.nodeIds[0] === 'block:f' ? answer('block:f', ['block:r', 'block:g']) : answer(req.nodeIds[0], []))),
      resolve: vi.fn(),
      revalidate: vi.fn(),
    } as unknown as ExplorerApi;
    const controller = new ExplorerController(api);
    await controller.open('block:r');
    await controller.open('block:f'); // trail r, f; the view holds f, r, g
    controller.revoke(controller.ids(), ['block:r', 'block:g']); // f is taken away: r and g lose their only link
    await vi.waitFor(() => expect(useGraphStore.getState().node).toBe('block:r'));
    expect(useGraphStore.getState().trail).toEqual(['block:r']);
    expect(ids(controller)).toContain('block:r');
    expect(controller.getSnapshot().failure).toBeNull();
  });

  it('applyVisible tells the nodes the check took away from those it only left alone', () => {
    const model = mergeExpand(emptyModel(), { source: 'code', nodeIds: ['f'] }, answer('f', ['r', 'g']));
    const out = applyVisible(model, ['f', 'r', 'g'], ['r', 'g']);
    expect(out.revoked).toEqual(['f']);
    expect(out.pruned.sort()).toEqual(['g', 'r']);
  });
});

describe('a later filter change is not undone by an earlier one that was superseded', () => {
  it('leaves the controls on the last filter chosen, whatever order the answers come back in', async () => {
    let release!: (r: ExpandResponse) => void;
    const slow = new Promise<ExpandResponse>((resolve) => (release = resolve));
    let n = 0;
    const api = {
      expand: vi.fn(async (req: ExpandRequest) => (++n === 2 ? slow : answer(req.nodeIds[0], [`${req.nodeIds[0]}.a`]))),
      resolve: vi.fn(),
      revalidate: vi.fn(),
    } as unknown as ExplorerApi;
    const controller = new ExplorerController(api);
    await controller.open('block:a');
    const first = controller.applyFilters({ direction: 'inbound' }); // slow
    const second = controller.applyFilters({ direction: 'outbound' });
    await second;
    release(answer('block:a', ['block:a.stale']));
    await first;
    expect(useGraphStore.getState().direction).toBe('outbound');
    expect(ids(controller)).not.toContain('block:a.stale');
  });
});

describe('a request is only second-guessed about what was revoked after it began, and a stale answer cannot release a newer guard', () => {
  it('lets a node back in through a request that started after access was restored', async () => {
    const { controller, state, api } = world();
    await controller.open('block:a');
    controller.revoke(controller.ids(), controller.ids().filter((id) => id !== 'block:a.a')); // a.a is revoked
    expect(ids(controller)).not.toContain('block:a.a');
    state.missing.clear();
    api.expand.mockImplementationOnce(async (req: ExpandRequest) => answer(req.nodeIds[0], ['block:a.a']));
    await controller.expand(['block:a.b']);
    expect(ids(controller)).toContain('block:a.a');
  });

  it('a late answer from an old view does not free the guard for the same page in the new view', async () => {
    const waiting: ((r: ExpandResponse) => void)[] = [];
    let calls = 0;
    const api = {
      expand: vi.fn(async (req: ExpandRequest) => {
        calls += 1;
        // Calls 2 (the old view's expand of x) and 4 (the new view's) wait to be answered.
        if (calls === 2 || calls === 4) return new Promise<ExpandResponse>((resolve) => waiting.push(resolve));
        return answer(req.nodeIds[0], []);
      }),
      resolve: vi.fn(),
      revalidate: vi.fn(),
    } as unknown as ExplorerApi & { expand: ReturnType<typeof vi.fn> };
    const controller = new ExplorerController(api);
    await controller.open('block:a');
    const old = controller.expand(['block:x']); // call 2, in flight
    await controller.open('block:b'); // a new view; its answer is call 3
    const newer = controller.expand(['block:x']); // call 4, in flight in the new view
    waiting[0](answer('block:x', ['block:x.old'])); // the old view's answer arrives late
    await old;
    await controller.expand(['block:x']); // the page is still in flight in the new view: no duplicate request
    expect(api.expand).toHaveBeenCalledTimes(4);
    waiting[1](answer('block:x', ['block:x.new']));
    await newer;
    expect(JSON.stringify(controller.getSnapshot().model)).not.toContain('x.old');
    expect(ids(controller)).toContain('block:x.new');
  });
});


describe('the browser Back and Forward follow the trail instead of adding to it', () => {
  const trailState = () => ({ trail: useGraphStore.getState().trail, at: useGraphStore.getState().trailIndex, node: useGraphStore.getState().node });

  it('moves to the place the history entry names, however far along, and adds a step only for an address with no place', async () => {
    const { controller } = world();
    for (const id of ['block:a', 'block:b', 'block:c']) await controller.open(id);
    expect(await controller.follow('block:a', 0)).toBe(true);
    expect(trailState()).toEqual({ trail: ['block:a', 'block:b', 'block:c'], at: 0, node: 'block:a' });
    expect(await controller.follow('block:c', 2)).toBe(true);
    expect(trailState().at).toBe(2);
    expect(await controller.follow('block:z')).toBe(true); // a link with no place
    expect(trailState().trail).toEqual(['block:a', 'block:b', 'block:c', 'block:z']);
    expect(await controller.follow('block:b', 0)).toBe(true); // the entry's place holds another node: the nearest place that holds b
    expect(trailState().trail).toEqual(['block:a', 'block:b', 'block:c', 'block:z']);
    expect(trailState().at).toBe(1);
  });

  it('keeps the two places apart when the same node is on the trail twice', async () => {
    const { controller } = world();
    for (const id of ['block:a', 'block:b', 'block:a']) await controller.open(id);
    await controller.follow('block:b', 1);
    expect(trailState().at).toBe(1);
    await controller.follow('block:a', 2); // Forward from B reaches the later A, not the earlier one
    expect(trailState()).toEqual({ trail: ['block:a', 'block:b', 'block:a'], at: 2, node: 'block:a' });
  });

  it('a second Back while the first is still loading does not add a copy of the trail', async () => {
    const { controller, state } = world();
    for (const id of ['block:a', 'block:b', 'block:c']) await controller.open(id);
    let release!: () => void;
    state.gate.set('block:b', new Promise<void>((resolve) => (release = resolve)));
    const first = controller.follow('block:b', 1); // still loading
    const second = controller.follow('block:a', 0);
    release();
    await Promise.all([first, second]);
    expect(trailState()).toEqual({ trail: ['block:a', 'block:b', 'block:c'], at: 0, node: 'block:a' });
  });

  it('Retry after a failed Back repeats the same move on the trail', async () => {
    const { controller, state } = world();
    for (const id of ['block:a', 'block:b']) await controller.open(id);
    state.down = true;
    expect(await controller.follow('block:a', 0)).toBe(false);
    expect(trailState()).toEqual({ trail: ['block:a', 'block:b'], at: 1, node: 'block:b' });
    state.down = false;
    await controller.retry();
    expect(trailState()).toEqual({ trail: ['block:a', 'block:b'], at: 0, node: 'block:a' });
  });
});


describe('the toolbar arrows keep the trail honest', () => {
  const at = () => ({ trail: useGraphStore.getState().trail, index: useGraphStore.getState().trailIndex, node: useGraphStore.getState().node });

  it('Retry after a failed Back moves along the trail, not just the picture', async () => {
    const { controller, state } = world();
    for (const id of ['block:a', 'block:b']) await controller.open(id);
    state.down = true;
    expect(await controller.go(-1)).toBe(false);
    expect(at()).toEqual({ trail: ['block:a', 'block:b'], index: 1, node: 'block:b' });
    state.down = false;
    await controller.retry();
    expect(at()).toEqual({ trail: ['block:a', 'block:b'], index: 0, node: 'block:a' });
  });

  it('lands on the node when a revoke shifted the trail while the load was in flight', async () => {
    const { controller, state } = world();
    for (const id of ['block:a', 'block:b', 'block:c']) await controller.open(id);
    let release!: () => void;
    state.gate.set('block:b', new Promise<void>((resolve) => (release = resolve)));
    const back = controller.go(-1);
    useGraphStore.getState().forget(['block:a']); // the trail is now [b, c] and its index moved
    release();
    expect(await back).toBe(true);
    const now = at();
    expect(now.node).toBe('block:b');
    expect(now.trail[now.index]).toBe('block:b');
  });
});


describe('a Back or Forward over an unavailable node skips every copy of it', () => {
  const at = () => ({ trail: useGraphStore.getState().trail, index: useGraphStore.getState().trailIndex, node: useGraphStore.getState().node });

  it('Back from C over a node that is on the trail twice lands on B', async () => {
    const { controller, state } = world();
    for (const id of ['block:a', 'block:b', 'block:a', 'block:c']) await controller.open(id);
    state.missing.add('block:a');
    expect(await controller.go(-1)).toBe(true);
    expect(at()).toEqual({ trail: ['block:b', 'block:c'], index: 0, node: 'block:b' });
  });

  it('Forward from B over a node that is on the trail twice lands on C', async () => {
    const { controller, state } = world();
    for (const id of ['block:a', 'block:b', 'block:a', 'block:c']) await controller.open(id);
    useGraphStore.getState().jumpTo(1); // sitting on B
    state.missing.add('block:a');
    expect(await controller.go(1)).toBe(true);
    expect(at()).toEqual({ trail: ['block:b', 'block:c'], index: 1, node: 'block:c' });
  });
});


describe('a move along the trail is one step for the address', () => {
  it('raises moving until the move has landed, so a trail compacted on the way is never written to the address', async () => {
    const { controller, state } = world();
    for (const id of ['block:a', 'block:b', 'block:c']) await controller.open(id);
    state.missing.add('block:b');
    const seen: boolean[] = [];
    controller.subscribe(() => seen.push(controller.getSnapshot().moving));
    expect(controller.getSnapshot().moving).toBe(false);
    const back = controller.go(-1);
    expect(controller.getSnapshot().moving).toBe(true);
    expect(await back).toBe(true);
    expect(controller.getSnapshot().moving).toBe(false);
    expect(seen.at(-1)).toBe(false);
    expect(seen.includes(true)).toBe(true);
  });

  it('follow lands on the nearest place that holds the node when the entry\'s place has shifted, even if it is already the centre', async () => {
    const { controller } = world();
    for (const id of ['block:a', 'block:b', 'block:c']) await controller.open(id);
    expect(await controller.follow('block:c', 5)).toBe(true); // a stale place past the end
    const s = useGraphStore.getState();
    expect(s.trail).toEqual(['block:a', 'block:b', 'block:c']);
    expect(s.trailIndex).toBe(2);
  });
});
