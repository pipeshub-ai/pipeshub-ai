import type { Node } from '@xyflow/react';
import type { FlowNodeData } from './types';

const SNAP = 20;
const GAP = 16;

export function snapToFlowGrid(x: number, y: number, grid = SNAP): { x: number; y: number } {
  return {
    x: Math.round(x / grid) * grid,
    y: Math.round(y / grid) * grid,
  };
}

function footprintFromTypeString(t: string): { w: number; h: number } {
  if (t === 'agent-core') return { w: 340, h: 560 };
  if (t.startsWith('toolset-')) return { w: 340, h: 400 };
  if (t.startsWith('tool-group-')) return { w: 276, h: 320 };
  if (t === 'kb-group' || t === 'app-group') return { w: 276, h: 300 };
  return { w: 276, h: 240 };
}

/** Layout footprint in flow coordinates (matches card widths in node components; height is a safe upper bound). */
export function approximateNodeFootprint(data: FlowNodeData): { w: number; h: number } {
  return footprintFromTypeString(data.type ?? '');
}

function rectsOverlap(
  a: { x: number; y: number; w: number; h: number },
  b: { x: number; y: number; w: number; h: number }
): boolean {
  const p = GAP;
  return !(a.x + a.w + p <= b.x || b.x + b.w + p <= a.x || a.y + a.h + p <= b.y || b.y + b.h + p <= a.y);
}

/**
 * Places the top-left of a new node: centered on the pointer, snapped to grid, then nudged if it overlaps existing nodes.
 */
export function resolvePremiumDropPosition(
  flowPointer: { x: number; y: number },
  plannedType: string,
  existingNodes: Node<FlowNodeData>[]
): { x: number; y: number } {
  const dims = footprintFromTypeString(plannedType);
  const x0 = flowPointer.x - dims.w / 2;
  const y0 = flowPointer.y - dims.h / 2;
  const { x, y } = snapToFlowGrid(x0, y0);

  const overlaps = (nx: number, ny: number) => {
    const rect = { x: nx, y: ny, w: dims.w, h: dims.h };
    return existingNodes.some((n) => {
      const fd = n.data as FlowNodeData;
      const fp = approximateNodeFootprint(fd);
      return rectsOverlap(rect, { x: n.position.x, y: n.position.y, w: fp.w, h: fp.h });
    });
  };

  if (!overlaps(x, y)) return { x, y };

  const col0 = x;
  let cx = x;
  let cy = y;
  for (let i = 0; i < 64; i++) {
    // Prefer sliding right (lane metaphor), then step down a row
    if (i % 5 !== 4) {
      cx += SNAP;
    } else {
      cx = col0;
      cy += SNAP;
    }
    const s = snapToFlowGrid(cx, cy);
    cx = s.x;
    cy = s.y;
    if (!overlaps(cx, cy)) return { x: cx, y: cy };
  }

  return { x, y };
}
