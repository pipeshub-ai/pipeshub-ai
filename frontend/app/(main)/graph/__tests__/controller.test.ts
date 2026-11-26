import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ErrorType, type ProcessedError } from '@/lib/api';
import { ExplorerController, MAX_MODEL_NODES, type ExplorerApi } from '../controller';
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

const failure = (statusCode: number): ProcessedError => ({ type: ErrorType.UNKNOWN_ERROR, message: 'x', statusCode });

function setup(expand: ExplorerApi['expand']) {
  const api = { expand: vi.fn(expand), revalidate: vi.fn() } as unknown as ExplorerApi & { expand: ReturnType<typeof vi.fn> };
  return { api, controller: new ExplorerController(api) };
}

beforeEach(() => useGraphStore.getState().reset());

describe('ExplorerController', () => {
  it('opens a node: puts it on the trail, expands it and holds the model', async () => {
    const { controller, api } = setup(async (req) => answer(req.nodeIds[0], ['block:b', 'block:c']));
    await controller.open('block:a');
    expect(useGraphStore.getState().trail).toEqual(['block:a']);
    expect(Object.keys(controller.getSnapshot().model.nodes).sort()).toEqual(['block:a', 'block:b', 'block:c']);
    expect(controller.getSnapshot().loading).toBe(false);
    expect(api.expand.mock.calls[0][0]).toMatchObject({ source: 'code', nodeIds: ['block:a'], direction: 'any', includeTests: false, limit: 50 });
  });

  it('sends the filters and no edgeTypes when none is chosen', async () => {
    const { controller, api } = setup(async (req) => answer(req.nodeIds[0], []));
    useGraphStore.getState().setFilters({ direction: 'inbound', edgeTypes: ['CALLS'], includeTests: true });
    await controller.open('block:a');
    expect(api.expand.mock.calls[0][0]).toMatchObject({ direction: 'inbound', edgeTypes: ['CALLS'], includeTests: true });
    useGraphStore.getState().setFilters({ edgeTypes: [] });
    await controller.reload();
    expect(api.expand.mock.calls[1][0]).not.toHaveProperty('edgeTypes');
    expect(useGraphStore.getState().trail).toEqual(['block:a']);
  });

  it('sends more than 20 anchors as separate requests of at most 20', async () => {
    const { controller, api } = setup(async (req: ExpandRequest) => answer(req.nodeIds[0], []));
    await controller.expand(Array.from({ length: 45 }, (_, i) => `block:${i}`));
    expect(api.expand.mock.calls.map(([req]) => req.nodeIds.length).sort((a, b) => b - a)).toEqual([20, 20, 5]);
  });

  it('continues a truncated read with the same group and cursor, and drops the marker when it ends', async () => {
    let call = 0;
    const { controller, api } = setup(async (req) =>
      ++call === 1
        ? answer(req.nodeIds[0], ['block:b'], { page: { truncated: true, incomplete: false, nextCursor: 'c1' } })
        : answer(req.nodeIds[0], ['block:c']),
    );
    await controller.open('block:a');
    expect(controller.getSnapshot().model.more['block:a']).toEqual({ group: ['block:a'], cursor: 'c1' });
    await controller.showMore('block:a');
    expect(api.expand.mock.calls[1][0]).toMatchObject({ nodeIds: ['block:a'], cursor: 'c1' });
    expect(controller.getSnapshot().model.more['block:a']).toBeUndefined();
    expect(Object.keys(controller.getSnapshot().model.nodes)).toContain('block:c');
  });

  it('starts the walk again, silently, when the server refuses a cursor', async () => {
    const { controller, api } = setup(async (req) => {
      if (req.cursor) throw failure(400);
      return answer(req.nodeIds[0], ['block:b'], { page: { truncated: true, incomplete: false, nextCursor: 'c1' } });
    });
    await controller.open('block:a');
    await controller.showMore('block:a');
    expect(api.expand.mock.calls.map(([req]) => req.cursor)).toEqual([undefined, 'c1', undefined]);
    expect(controller.getSnapshot().failure).toBeNull();
    expect(controller.getSnapshot().loading).toBe(false);
  });

  it('keeps what is on screen when a read fails, and says it failed (never an empty graph)', async () => {
    let fail = false;
    const { controller } = setup(async (req) => {
      if (fail) throw failure(503);
      return answer(req.nodeIds[0], ['block:b']);
    });
    await controller.open('block:a');
    fail = true;
    await controller.expand(['block:b']);
    expect(controller.getSnapshot().failure).toBe('unavailable');
    expect(Object.keys(controller.getSnapshot().model.nodes)).toEqual(['block:a', 'block:b']);
  });

  it('forgets a node the server calls unavailable, from the trail too, whether it is hidden or missing', async () => {
    const { controller } = setup(async () => {
      throw failure(404);
    });
    await controller.open('block:secret');
    expect(controller.getSnapshot().failure).toBe('not-available');
    expect(useGraphStore.getState().trail).toEqual([]);
    expect(useGraphStore.getState().node).toBeNull();
  });

  it('ignores an answer that arrives after the view moved on', async () => {
    let release!: (r: ExpandResponse) => void;
    const slow = new Promise<ExpandResponse>((resolve) => (release = resolve));
    let n = 0;
    const { controller } = setup(async (req) => (++n === 1 ? slow : answer(req.nodeIds[0], ['block:fresh'])));
    const first = controller.open('block:old');
    await controller.open('block:new');
    release(answer('block:old', ['block:stale']));
    await first;
    expect(Object.keys(controller.getSnapshot().model.nodes).sort()).toEqual(['block:fresh', 'block:new']);
  });

  it('drops revoked nodes from the model, the selection and the trail', async () => {
    const { controller } = setup(async (req) => answer(req.nodeIds[0], ['block:b', 'block:secret']));
    await controller.open('block:a');
    useGraphStore.getState().focusOn('block:secret');
    controller.revoke(controller.ids(), ['block:a', 'block:b']);
    expect(Object.keys(controller.getSnapshot().model.nodes).sort()).toEqual(['block:a', 'block:b']);
    expect(JSON.stringify(controller.getSnapshot().model)).not.toContain('secret');
    expect(useGraphStore.getState().trail).toEqual(['block:a']);
  });

  it('clears everything when access can no longer be confirmed', async () => {
    const { controller } = setup(async (req) => answer(req.nodeIds[0], ['block:b']));
    await controller.open('block:a');
    controller.clear();
    expect(controller.ids()).toEqual([]);
  });

  it('notifies subscribers and stops after unsubscribe', async () => {
    const { controller } = setup(async (req) => answer(req.nodeIds[0], []));
    const listener = vi.fn();
    const off = controller.subscribe(listener);
    await controller.open('block:a');
    expect(listener).toHaveBeenCalled();
    off();
    listener.mockClear();
    await controller.open('block:b');
    expect(listener).not.toHaveBeenCalled();
  });
});

describe('ExplorerController.openAddress', () => {
  const resolving = (resolve: ExplorerApi['resolve'], expand: ExplorerApi['expand'] = async (req) => answer(req.nodeIds[0], ['block:b'])) => {
    const api = { expand: vi.fn(expand), resolve: vi.fn(resolve), revalidate: vi.fn() } as unknown as ExplorerApi;
    return { api, controller: new ExplorerController(api) };
  };

  it('turns an address into a node and opens it like any other', async () => {
    const { controller, api } = resolving(async () => ({ nodeId: 'block:found' }));
    await controller.openAddress({ source: 'code', recordId: 'rec1', includeTests: true });
    expect(api.resolve).toHaveBeenCalledWith({ source: 'code', recordId: 'rec1', includeTests: true });
    expect(useGraphStore.getState().trail).toEqual(['block:found']);
    expect(Object.keys(controller.getSnapshot().model.nodes)).toContain('block:found');
    expect(controller.getSnapshot().loading).toBe(false);
  });

  it('says "not available" for hidden and missing alike, and opens nothing', async () => {
    const { controller, api } = resolving(async () => {
      throw failure(404);
    });
    await controller.openAddress({ source: 'code', repo: 'acme/shop', filePath: 'src/a.py' });
    expect(controller.getSnapshot().failure).toBe('not-available');
    expect(useGraphStore.getState().trail).toEqual([]);
    expect(api.expand).not.toHaveBeenCalled();
    expect(controller.getSnapshot().loading).toBe(false);
  });

  it('names an ambiguous repo as its own state', async () => {
    const { controller } = resolving(async () => {
      throw { ...failure(409), message: "'shop' matches 2 repositories: A:acme/shop, B:acme/shop." };
    });
    await controller.openAddress({ source: 'code', repo: 'shop', filePath: 'a.py' });
    expect(controller.getSnapshot().failure).toBe('ambiguous');
    const { controller: other } = resolving(async () => {
      throw { ...failure(503), message: 'internal detail' };
    });
    await other.openAddress({ source: 'code', recordId: 'r' });
    expect(other.getSnapshot().failure).toBe('unavailable');
  });

  it('ignores an answer for an address the person has moved on from', async () => {
    let release!: (r: { nodeId: string }) => void;
    const slow = new Promise<{ nodeId: string }>((resolve) => (release = resolve));
    let n = 0;
    const { controller } = resolving(async () => (++n === 1 ? slow : { nodeId: 'block:new' }));
    const first = controller.openAddress({ source: 'code', recordId: 'old' });
    await controller.openAddress({ source: 'code', recordId: 'new' });
    release({ nodeId: 'block:old' });
    await first;
    expect(useGraphStore.getState().trail).toEqual(['block:new']);
  });
});

describe('ExplorerController view ceiling', () => {
  it('stops asking once the view holds as many nodes as it may, and says so', async () => {
    const big = (anchor: string) => ({
      ...answer(anchor, []),
      nodes: Array.from({ length: MAX_MODEL_NODES }, (_, i) => ({ id: `block:n${i}`, label: `n${i}` })),
    });
    const { controller, api } = setup(async (req) => big(req.nodeIds[0]));
    await controller.open('block:n0');
    expect(api.expand).toHaveBeenCalledTimes(1);
    await controller.expand(['block:n1']);
    expect(api.expand).toHaveBeenCalledTimes(1);
    expect(controller.getSnapshot().failure).toBe('full');
    expect(controller.getSnapshot().loading).toBe(false);
  });
});
