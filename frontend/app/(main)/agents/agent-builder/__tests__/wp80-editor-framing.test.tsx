/**
 * WP-80: the agent editor opens readable. Round 15's run settings made the agent card so tall that the editor's
 * first fitView shrank the whole flow to an unreadable sliver, and "Engine settings" fell below the canvas (AG1 PW-1
 * failed 2 of 3 gates). Which zoom it opened at depended on whether the card had grown before the fit.
 *
 * WP80-ED-1 a flow that fits at a readable zoom is framed whole and centered, as fitView frames it
 * WP80-ED-2 a flow that would need a smaller zoom opens at the readable zoom with the agent card's top at the top of
 *           the canvas, centered on the card; never below the readable zoom however tall the card is
 * WP80-ED-3 never above the largest zoom; no canvas size yet means no framing
 * WP80-ED-4 the editor frames once, after React Flow has measured every node (not after a fixed number of frames),
 *           on the agent card; it frames again only after the flow empties and hydrates again
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { INITIAL_FLOW_FRAMING, initialFlowViewport } from '../flow-framing';
import { InitialFlowFraming } from '../components/initial-flow-framing';

const flow = vi.hoisted(() => ({
  initialized: false,
  width: 950,
  height: 560,
  nodes: [] as Array<{ id: string; data: { type: string } }>,
  bounds: new Map<string, { x: number; y: number; width: number; height: number }>(),
  setViewport: vi.fn(),
}));

vi.mock('@xyflow/react', () => ({
  useNodesInitialized: () => flow.initialized,
  useStore: (select: (s: { width: number; height: number; nodes: unknown[] }) => unknown) =>
    select({ width: flow.width, height: flow.height, nodes: flow.nodes }),
  useReactFlow: () => ({
    getNodes: () => flow.nodes,
    getNodesBounds: (nodes: Array<{ id: string }>) => {
      const rects = nodes.map((n) => flow.bounds.get(n.id)!);
      const x = Math.min(...rects.map((r) => r.x));
      const y = Math.min(...rects.map((r) => r.y));
      return {
        x,
        y,
        width: Math.max(...rects.map((r) => r.x + r.width)) - x,
        height: Math.max(...rects.map((r) => r.y + r.height)) - y,
      };
    },
    setViewport: flow.setViewport,
  }),
}));

const { padding, readableZoom, maxZoom } = INITIAL_FLOW_FRAMING;

describe('initialFlowViewport', () => {
  it('WP80-ED-1: a flow that fits at a readable zoom is framed whole and centered', () => {
    const graph = { x: 50, y: 120, width: 1110, height: 600 };
    const v = initialFlowViewport({ graph, focus: { x: 420, y: 120, width: 340, height: 600 }, width: 1600, height: 900 })!;
    const expected = Math.min((1600 * (1 - 2 * padding)) / 1110, (900 * (1 - 2 * padding)) / 600);
    expect(v.zoom).toBeCloseTo(expected);
    expect(v.zoom).toBeGreaterThanOrEqual(readableZoom);
    expect(v.x + (graph.x + graph.width / 2) * v.zoom).toBeCloseTo(800);
    expect(v.y + (graph.y + graph.height / 2) * v.zoom).toBeCloseTo(450);
  });

  it('WP80-ED-2: a tall agent card opens readable, its top at the top of the canvas, centered on the card', () => {
    // The gate's failure: a 340-wide card several thousand pixels tall on a 950 x 560 canvas.
    const focus = { x: 420, y: 120, width: 340, height: 4200 };
    for (const height of [4200, 12_000]) {
      const v = initialFlowViewport({
        graph: { x: 50, y: 120, width: 1110, height },
        focus: { ...focus, height },
        width: 950,
        height: 560,
      })!;
      expect(v.zoom).toBe(readableZoom);
      expect(v.y + focus.y * v.zoom).toBeCloseTo(560 * padding);
      expect(v.x + (focus.x + focus.width / 2) * v.zoom).toBeCloseTo(475);
    }
  });

  it('WP80-ED-3: never above the largest zoom; no canvas size, no framing', () => {
    const tiny = { x: 0, y: 0, width: 100, height: 80 };
    expect(initialFlowViewport({ graph: tiny, focus: tiny, width: 1600, height: 900 })!.zoom).toBe(maxZoom);
    expect(initialFlowViewport({ graph: tiny, focus: tiny, width: 0, height: 0 })).toBeNull();
  });
});

describe('InitialFlowFraming', () => {
  beforeEach(() => {
    flow.initialized = false;
    flow.width = 950;
    flow.height = 560;
    flow.nodes = [
      { id: 'chat-input-1', data: { type: 'user-input' } },
      { id: 'agent-core-1', data: { type: 'agent-core' } },
    ];
    flow.bounds = new Map([
      ['chat-input-1', { x: 50, y: 520, width: 260, height: 120 }],
      ['agent-core-1', { x: 420, y: 120, width: 340, height: 4200 }],
    ]);
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('WP80-ED-4: frames once, after every node is measured, on the agent card; again only after the flow empties', () => {
    const view = render(<InitialFlowFraming />);
    expect(flow.setViewport).not.toHaveBeenCalled();

    flow.initialized = true;
    view.rerender(<InitialFlowFraming />);
    expect(flow.setViewport).toHaveBeenCalledTimes(1);
    const [viewport] = flow.setViewport.mock.calls[0] as [{ x: number; y: number; zoom: number }];
    expect(viewport.zoom).toBe(readableZoom);
    expect(viewport.y + 120 * viewport.zoom).toBeCloseTo(560 * padding);
    expect(viewport.x + (420 + 170) * viewport.zoom).toBeCloseTo(475);

    // The card growing after the first frame (a section's data arriving) does not move the view under the person.
    flow.bounds.set('agent-core-1', { x: 420, y: 120, width: 340, height: 6000 });
    view.rerender(<InitialFlowFraming />);
    expect(flow.setViewport).toHaveBeenCalledTimes(1);

    flow.nodes = [];
    view.rerender(<InitialFlowFraming />);
    flow.nodes = [{ id: 'agent-core-1', data: { type: 'agent-core' } }];
    view.rerender(<InitialFlowFraming />);
    expect(flow.setViewport).toHaveBeenCalledTimes(2);
  });
});
