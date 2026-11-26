'use client';

import { useEffect, useRef } from 'react';
import { useNodesInitialized, useReactFlow, useStore } from '@xyflow/react';
import type { Node } from '@xyflow/react';
import type { FlowNodeData } from '../types';
import { initialFlowViewport } from '../flow-framing';

/**
 * Frames the flow once it has hydrated (WP-80). Waits for React Flow to measure every node: a fixed number of frames
 * framed whatever height the agent card had reached by then, so the opening zoom changed from one load to the next.
 * Rendered inside `<ReactFlow>`. `insetLeft` is the width of a palette floating over the canvas's left edge (tablets):
 * the flow is framed in the canvas to its right.
 */
export function InitialFlowFraming({ insetLeft = 0 }: { insetLeft?: number }) {
  const initialized = useNodesInitialized();
  const width = useStore((s) => s.width);
  const height = useStore((s) => s.height);
  const count = useStore((s) => s.nodes.length);
  const { getNodes, getNodesBounds, setViewport } = useReactFlow<Node<FlowNodeData>>();
  const framed = useRef(false);

  useEffect(() => {
    if (count === 0) {
      framed.current = false;
      return;
    }
    if (framed.current || !initialized) return;
    const nodes = getNodes();
    const agent = nodes.find((n) => n.data?.type === 'agent-core');
    const viewport = initialFlowViewport({
      graph: getNodesBounds(nodes),
      focus: agent ? getNodesBounds([agent]) : null,
      width: width - insetLeft,
      height,
    });
    if (!viewport) return;
    framed.current = true;
    void setViewport({ ...viewport, x: viewport.x + insetLeft });
    // insetLeft is read for the first framing only; toggling the palette must not reframe.
  }, [count, initialized, width, height, getNodes, getNodesBounds, setViewport]);

  return null;
}
