import { describe, expect, it, vi } from 'vitest';
import { diffModel } from '../engine/model-diff';
import { buildStyle, createCytoscapeEngine } from '../engine/cytoscape-engine';
import { qualityFor } from '../engine/tiers';
import { emptyModel, mergeExpand, removeNodes } from '../model/graph-model';
import { CELL_HEIGHT, CELL_WIDTH } from '../model/placement';
import type { ExpandResponse } from '../types';

const answer = (over: Partial<ExpandResponse> = {}): ExpandResponse => ({
  nodes: [
    { id: 'block:main', label: 'main', kind: 'function' },
    { id: 'block:lib', label: 'lib', kind: 'class' },
  ],
  edges: [
    { id: 'e:1', from: 'block:main', to: 'block:lib', type: 'CALLS', confidence: 'INFERRED' },
  ],
  anchors: [{ id: 'block:main', total: 1 }],
  page: { truncated: false, incomplete: false },
  graphStatus: 'ready',
  ...over,
});

const built = () => mergeExpand(emptyModel(), { source: 'code', nodeIds: ['block:main'] }, answer());

describe('diffModel', () => {
  it('adds everything the first time, with positions from the grid', () => {
    const diff = diffModel(null, built());
    expect(diff.addNodes.map((n) => n.data.id).sort()).toEqual(['block:lib', 'block:main']);
    expect(diff.addEdges).toHaveLength(1);
    expect(diff.addEdges[0].data).toMatchObject({ source: 'block:main', target: 'block:lib', inferred: 1 });
    expect(diff.addNodes.every((n) => Number.isFinite(n.position?.x) && Number.isFinite(n.position?.y))).toBe(true);
  });

  it('adds only what is new, so nodes already drawn are not touched', () => {
    const first = built();
    const second = mergeExpand(first, { source: 'code', nodeIds: ['block:main'] }, answer({
      nodes: [{ id: 'block:main', label: 'main', kind: 'function' }, { id: 'block:new', label: 'new' }],
      edges: [{ id: 'e:2', from: 'block:main', to: 'block:new', type: 'CALLS' }],
    }));
    const diff = diffModel(first, second);
    expect(diff.addNodes.map((n) => n.data.id)).toEqual(['block:new']);
    expect(diff.addEdges.map((e) => e.data.id)).toEqual(['e:2']);
    expect(diff.removeIds).toEqual([]);
  });

  it('removes a revoked node and its edges', () => {
    const first = built();
    const diff = diffModel(first, removeNodes(first, ['block:lib']));
    expect(diff.removeIds.sort()).toEqual(['block:lib', 'e:1']);
  });

  it('updates a node whose record arrived fuller', () => {
    const first = built();
    const second = mergeExpand(first, { source: 'code', nodeIds: ['block:main'] }, answer({
      nodes: [{ id: 'block:main', label: 'main_renamed', kind: 'function' }, { id: 'block:lib', label: 'lib', kind: 'class' }],
    }));
    expect(diffModel(first, second).updateNodes).toEqual([
      { id: 'block:main', data: expect.objectContaining({ label: 'main_renamed' }) },
    ]);
  });

  it('draws inferred links dashed and solid links solid', () => {
    const style = JSON.stringify(buildStyle());
    expect(style).toContain('edge[inferred = 1]');
    expect(style).toContain('dashed');
  });
});

describe('cytoscape engine (headless)', () => {
  it('draws the model, applies a revocation, and tears down', async () => {
    const engine = createCytoscapeEngine({ headless: true });
    await engine.mount(document.createElement('div'), qualityFor({ hasWebgl2: false, renderer: '', devicePixelRatio: 1, reducedMotion: false }, 'canvas', 0), {
      onSelect: () => undefined, onExpand: () => undefined, onHover: () => undefined, onFrame: () => undefined,
    });
    const model = built();
    engine.setModel(model);
    engine.setModel(removeNodes(model, ['block:lib']));
    engine.select('block:main');
    engine.reveal('block:main');
    engine.reveal();
    engine.destroy();
  });
});

describe('capModel and the table page', () => {
  const many = (n: number) =>
    mergeExpand(
      emptyModel(),
      { source: 'code', nodeIds: ['block:n0'] },
      {
        nodes: Array.from({ length: n }, (_, i) => ({ id: `block:n${i}`, label: `n${i}` })),
        edges: Array.from({ length: n - 1 }, (_, i) => ({ id: `e:${i}`, from: 'block:n0', to: `block:n${i + 1}`, type: 'CALLS' })),
        anchors: [{ id: 'block:n0', total: n - 1 }],
        page: { truncated: false, incomplete: false },
        graphStatus: 'ready',
      },
    );

  it('keeps the first nodes that arrived and the edges between them', async () => {
    const { capModel } = await import('../engine/model-diff');
    const capped = capModel(many(10), 4);
    expect(Object.keys(capped.nodes)).toEqual(['block:n0', 'block:n1', 'block:n2', 'block:n3']);
    expect(Object.keys(capped.edges)).toHaveLength(3);
  });

  it('leaves a model under the cap as it was, and draws nothing new when the cap holds', async () => {
    const { capModel } = await import('../engine/model-diff');
    const model = many(5);
    expect(capModel(model, 100)).toBe(model);
    const grown = mergeExpand(model, { source: 'code', nodeIds: ['block:n0'] }, {
      nodes: [{ id: 'block:n0' }, { id: 'block:x1' }],
      edges: [{ id: 'e:x', from: 'block:n0', to: 'block:x1', type: 'CALLS' }],
      anchors: [], page: { truncated: false, incomplete: false }, graphStatus: 'ready',
    });
    // At a cap of 5, the sixth node is in the model (and the table) but not in the picture.
    const diff = diffModel(capModel(model, 5), capModel(grown, 5));
    expect(diff.addNodes).toEqual([]);
    expect(diff.addEdges).toEqual([]);
  });
});


describe('a new view is laid out afresh', () => {
  const moved = (base: ReturnType<typeof built>, view: number, positions: ReturnType<typeof built>['positions']) => ({ ...base, view, positions });

  it('moves a node that was already drawn to its cell in the new view, and only then', () => {
    const before = built();
    const shifted = Object.fromEntries(Object.entries(before.positions).map(([id, c]) => [id, { c: c.c + 3, r: c.r + 2 }]));
    const newView = diffModel(before, moved(before, before.view + 1, shifted));
    expect(newView.moveNodes.map((m) => m.id).sort()).toEqual(['block:lib', 'block:main']);
    expect(newView.addNodes).toEqual([]);
    const sameView = diffModel(before, moved(before, before.view, shifted));
    expect(sameView.moveNodes).toEqual([]);
  });

  it('puts the survivor of a new view on the new grid in the drawing too', async () => {
    let cy!: import('cytoscape').Core;
    const engine = createCytoscapeEngine({ headless: true, expose: (c) => (cy = c) });
    await engine.mount(document.createElement('div'), qualityFor({ hasWebgl2: false, renderer: '', devicePixelRatio: 1, reducedMotion: false }, 'canvas', 0), {
      onSelect: () => undefined, onExpand: () => undefined, onHover: () => undefined, onFrame: () => undefined,
    });
    const before = built();
    engine.setModel(before);
    const shifted = Object.fromEntries(Object.entries(before.positions).map(([id, c]) => [id, { c: c.c + 3, r: c.r + 2 }]));
    engine.setModel(moved(before, before.view + 1, shifted));
    const cell = shifted['block:main'];
    expect(cy.getElementById('block:main').position()).toEqual({ x: cell.c * CELL_WIDTH, y: cell.r * CELL_HEIGHT });
    engine.destroy();
  });
});


describe('the engine on its own', () => {
  const canvas = qualityFor({ hasWebgl2: false, renderer: '', devicePixelRatio: 1, reducedMotion: false }, 'canvas', 0);
  const handlers = { onSelect: () => undefined, onExpand: () => undefined, onHover: () => undefined, onFrame: () => undefined };
  type Rule = { selector: string; style: Record<string, unknown> };

  it('draws the focus ring in the theme colour, so it shows on a dark page too', () => {
    const focus = (buildStyle({ text: '#ededed', background: '#111111' }) as unknown as Rule[]).find((r) => r.selector === 'node.focus');
    expect(focus?.style['border-color']).toBe('#ededed');
  });

  it('builds nothing when destroy() ran while the library was still loading', async () => {
    let created = 0;
    const engine = createCytoscapeEngine({ headless: true, expose: () => { created += 1; } });
    const mounting = engine.mount(document.createElement('div'), canvas, handlers);
    engine.destroy(); // before the import resolves, as a replayed effect or a quick toggle does
    await mounting;
    expect(created).toBe(0);
  });

  it('turns fits and centring into jumps for reduced motion, and animates them otherwise', async () => {
    const run = async (reducedMotion: boolean) => {
      let cy!: import('cytoscape').Core;
      const engine = createCytoscapeEngine({ headless: true, expose: (c) => (cy = c) });
      await engine.mount(document.createElement('div'), qualityFor({ hasWebgl2: true, renderer: 'GPU', devicePixelRatio: 1, reducedMotion }, 'gpu', 0), handlers);
      engine.setModel(built());
      const spy = vi.spyOn(cy, 'animate');
      engine.reveal('block:main');
      const duration = (spy.mock.calls[0][0] as { duration: number }).duration;
      engine.destroy();
      return duration;
    };
    expect(await run(true)).toBe(0);
    expect(await run(false)).toBeGreaterThan(0);
  });
});
