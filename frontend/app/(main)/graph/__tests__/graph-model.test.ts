import { describe, expect, it } from 'vitest';
import { applyVisible, emptyModel, mergeExpand, nodeCount, removeNodes } from '../model/graph-model';
import { cellKey } from '../model/placement';
import type { ExpandRequest, ExpandResponse, GraphEdge, GraphNode } from '../types';

const node = (id: string): GraphNode => ({ id: `block:${id}`, label: id });
const edge = (from: string, to: string, type = 'CALLS'): GraphEdge => ({
  id: `e:${from}-${to}-${type}`,
  from: `block:${from}`,
  to: `block:${to}`,
  type,
});

function response(over: Partial<ExpandResponse> & Pick<ExpandResponse, 'nodes' | 'edges'>): ExpandResponse {
  return {
    anchors: [],
    page: { truncated: false, incomplete: false },
    graphStatus: 'ready',
    ...over,
  };
}

const request = (...ids: string[]): ExpandRequest => ({ source: 'code', nodeIds: ids.map((i) => `block:${i}`) });

describe('mergeExpand', () => {
  it('adds nodes and edges and places callers left and callees right of the anchor', () => {
    const model = mergeExpand(
      emptyModel(),
      request('main'),
      response({
        nodes: [node('main'), node('caller'), node('callee')],
        edges: [edge('caller', 'main'), edge('main', 'callee')],
        anchors: [{ id: 'block:main', total: 2 }],
      }),
    );
    const at = (id: string) => model.positions[`block:${id}`];
    expect(nodeCount(model)).toBe(3);
    expect(at('caller').c).toBeLessThan(at('main').c);
    expect(at('callee').c).toBeGreaterThan(at('main').c);
    expect(model.totals['block:main']).toBe(2);
  });

  it('never moves a node that is placed, and never overlaps two', () => {
    const first = mergeExpand(
      emptyModel(),
      request('main'),
      response({ nodes: [node('main'), node('a'), node('b')], edges: [edge('main', 'a'), edge('main', 'b')] }),
    );
    const second = mergeExpand(
      first,
      request('main'),
      response({
        nodes: [node('main'), node('c'), node('d'), node('e')],
        edges: [edge('main', 'c'), edge('main', 'd'), edge('main', 'e')],
      }),
    );
    for (const id of Object.keys(first.positions)) expect(second.positions[id]).toEqual(first.positions[id]);
    const cells = Object.values(second.positions).map(cellKey);
    expect(new Set(cells).size).toBe(cells.length);
  });

  it('merges the same edge seen twice into one', () => {
    const one = mergeExpand(emptyModel(), request('a'), response({ nodes: [node('a'), node('b')], edges: [edge('a', 'b')] }));
    const two = mergeExpand(one, request('b'), response({ nodes: [node('a'), node('b')], edges: [edge('a', 'b')] }));
    expect(Object.keys(two.edges)).toHaveLength(1);
    expect(nodeCount(two)).toBe(2);
  });

  it('shows no total for a truncated read and remembers how to continue it', () => {
    const model = mergeExpand(
      emptyModel(),
      request('hub'),
      response({
        nodes: [node('hub'), node('a')],
        edges: [edge('hub', 'a')],
        anchors: [{ id: 'block:hub', total: 50 }],
        page: { truncated: true, incomplete: false, nextCursor: 'cur1' },
      }),
    );
    expect(model.totals['block:hub']).toBeUndefined();
    expect(model.more['block:hub']).toEqual({ group: ['block:hub'], cursor: 'cur1' });
    const done = mergeExpand(
      model,
      { ...request('hub'), cursor: 'cur1' },
      response({ nodes: [node('hub'), node('b')], edges: [edge('hub', 'b')], anchors: [{ id: 'block:hub', total: 2 }] }),
    );
    expect(done.more['block:hub']).toBeUndefined();
    expect(done.totals['block:hub']).toBe(2);
  });

  it('keeps incomplete once any answer said so, and takes the latest graph status', () => {
    const one = mergeExpand(
      emptyModel(),
      request('a'),
      response({ nodes: [node('a')], edges: [], page: { truncated: false, incomplete: true }, graphStatus: 'stale' }),
    );
    const two = mergeExpand(one, request('a'), response({ nodes: [node('a')], edges: [], graphStatus: 'ready' }));
    expect(two.incomplete).toBe(true);
    expect(two.status).toBe('ready');
  });

  it('places a node with no edge to a placed node instead of dropping it', () => {
    const model = mergeExpand(emptyModel(), request('a'), response({ nodes: [node('a'), node('x')], edges: [] }));
    expect(model.positions['block:x']).toBeDefined();
  });
});

describe('revocation', () => {
  const built = () =>
    mergeExpand(
      emptyModel(),
      { source: 'code', nodeIds: ['block:main'], limit: 10 },
      response({
        nodes: [node('main'), node('lib'), node('secret')],
        edges: [edge('main', 'lib'), edge('main', 'secret'), edge('secret', 'lib')],
        anchors: [{ id: 'block:main', total: 2 }],
      }),
    );

  it('removes a node and every edge, position and total that touched it, leaving no trace', () => {
    const model = removeNodes(built(), ['block:secret']);
    expect(model.nodes['block:secret']).toBeUndefined();
    expect(model.positions['block:secret']).toBeUndefined();
    expect(JSON.stringify(model)).not.toContain('secret');
    expect(Object.keys(model.edges)).toHaveLength(1);
  });

  it('applies a revalidation: only asked-and-not-returned ids go, nodes added meanwhile stay', () => {
    const before = built();
    const asked = ['block:main', 'block:lib', 'block:secret'];
    const during = mergeExpand(before, request('lib'), response({ nodes: [node('lib'), node('fresh')], edges: [edge('lib', 'fresh')] }));
    const { model, revoked } = applyVisible(during, asked, ['block:main', 'block:lib']);
    expect(revoked).toEqual(['block:secret']);
    expect(model.nodes['block:fresh']).toBeDefined();
    expect(model.nodes['block:secret']).toBeUndefined();
  });

  it('drops a revoked anchor from the groups that continue a read', () => {
    const model = mergeExpand(
      emptyModel(),
      request('a', 'b'),
      response({
        nodes: [node('a'), node('b')],
        edges: [],
        page: { truncated: true, incomplete: false, nextCursor: 'c' },
      }),
    );
    const after = removeNodes(model, ['block:b']);
    expect(after.more['block:a'].group).toEqual(['block:a']);
    expect(after.more['block:b']).toBeUndefined();
  });
});
