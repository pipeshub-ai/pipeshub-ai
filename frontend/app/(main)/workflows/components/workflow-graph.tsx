'use client';

import React, { useId, useMemo } from 'react';
import * as dagre from 'dagre';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { WorkflowIR } from '../types';

interface WorkflowGraphProps {
  ir: WorkflowIR;
  onNodeClick?: (nodeId: string, sourceStart: number | null | undefined) => void;
  /** Node to outline, so graph and code stay visibly in sync. */
  selectedNodeId?: string | null;
  height?: string;
}

function getNodeColor(kind: string): string {
  switch (kind) {
    case 'workflow':    return 'var(--violet-9)';
    case 'step':        return 'var(--blue-9)';
    case 'tool_call':   return 'var(--green-9)';
    case 'agent_call':  return 'var(--orange-9)';
    case 'branch':      return 'var(--yellow-9)';
    case 'loop':        return 'var(--cyan-9)';
    case 'unresolved':  return 'var(--gray-9)';
    default:            return 'var(--gray-8)';
  }
}

function computeLayout(ir: WorkflowIR): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();

  try {
    const g = new dagre.graphlib.Graph();
    g.setDefaultEdgeLabel(() => ({}));
    g.setGraph({ rankdir: 'TB', ranksep: 60, nodesep: 40 });

    ir.nodes.forEach((node) => {
      g.setNode(node.node_id, { width: 160, height: 44 });
    });
    ir.edges.forEach((edge) => {
      g.setEdge(edge.from_node, edge.to_node);
    });

    dagre.layout(g);

    ir.nodes.forEach((node) => {
      const pos = g.node(node.node_id) as { x: number; y: number } | undefined;
      if (pos) positions.set(node.node_id, { x: pos.x, y: pos.y });
    });
  } catch (err) {
    // dagre can throw on a malformed/cyclic IR graph -- fall back to a
    // simple vertical layout rather than rendering nothing.
    console.warn('WorkflowGraph: dagre layout failed, falling back to vertical layout', err);
    ir.nodes.forEach((node, index) => {
      positions.set(node.node_id, { x: 80, y: index * 80 });
    });
  }

  return positions;
}

export function WorkflowGraph({
  ir,
  onNodeClick,
  selectedNodeId = null,
  height = '500px',
}: WorkflowGraphProps) {
  const { t } = useTranslation();
  const positions = useMemo(() => computeLayout(ir), [ir]);
  // Unique per-instance so multiple <WorkflowGraph> on the page (e.g. a
  // list of workflow cards) don't collide on the same `<marker id>`.
  const arrowheadId = useId();

  if (!ir.nodes.length) {
    return (
      <Flex align="center" justify="center" style={{ height }}>
        <Text color="gray" size="2">{t('workflowsPage.noGraph', 'No workflow structure to display.')}</Text>
      </Flex>
    );
  }

  const xs = Array.from(positions.values()).map((p) => p.x);
  const ys = Array.from(positions.values()).map((p) => p.y);
  const minX = Math.min(...xs) - 90;
  const minY = Math.min(...ys) - 30;
  const maxX = Math.max(...xs) + 90;
  const maxY = Math.max(...ys) + 30;
  const svgWidth = maxX - minX + 20;
  const svgHeight = maxY - minY + 20;

  return (
    <div
      style={{
        height,
        overflow: 'auto',
        background: 'var(--gray-a2)',
        borderRadius: 'var(--radius-3)',
        border: '1px solid var(--gray-a4)',
      }}
    >
      <svg
        width={svgWidth}
        height={svgHeight}
        viewBox={`${minX - 10} ${minY - 10} ${svgWidth} ${svgHeight}`}
        style={{ display: 'block' }}
      >
        <defs>
          <marker id={arrowheadId} markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto">
            <polygon points="0 0, 10 3.5, 0 7" fill="var(--gray-8)" />
          </marker>
        </defs>

        {ir.edges.map((edge, i) => {
          const from = positions.get(edge.from_node);
          const to = positions.get(edge.to_node);
          if (!from || !to) return null;
          return (
            <g key={`edge-${i}`}>
              <line
                x1={from.x}
                y1={from.y + 22}
                x2={to.x}
                y2={to.y - 22}
                stroke="var(--gray-8)"
                strokeWidth={1.5}
                markerEnd={`url(#${arrowheadId})`}
              />
              {edge.label && (
                <text
                  x={(from.x + to.x) / 2}
                  y={(from.y + to.y) / 2}
                  fontSize={10}
                  fill="var(--gray-9)"
                  textAnchor="middle"
                >
                  {edge.label}
                </text>
              )}
            </g>
          );
        })}

        {ir.nodes.map((node) => {
          const pos = positions.get(node.node_id);
          if (!pos) return null;
          const color = getNodeColor(node.kind);
          const isEntry = node.node_id === ir.entry_node_id;
          const isSelected = node.node_id === selectedNodeId;
          return (
            <g
              key={node.node_id}
              transform={`translate(${pos.x}, ${pos.y})`}
              style={{ cursor: onNodeClick ? 'pointer' : 'default' }}
              onClick={() => onNodeClick?.(node.node_id, node.source_start)}
            >
              {isSelected && (
                <rect
                  x={-79}
                  y={-24}
                  width={158}
                  height={48}
                  rx={9}
                  fill="none"
                  stroke="var(--violet-9)"
                  strokeWidth={2}
                />
              )}
              <rect
                x={-75}
                y={-20}
                width={150}
                height={40}
                rx={6}
                fill={isEntry ? color : 'var(--gray-1)'}
                stroke={color}
                strokeWidth={isEntry ? 0 : 2}
              />
              <text
                x={0}
                y={4}
                fontSize={11}
                fontWeight={500}
                fill={isEntry ? 'white' : color}
                textAnchor="middle"
              >
                {node.label.length > 22 ? node.label.slice(0, 20) + '…' : node.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
