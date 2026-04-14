'use client';

import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Background,
  ReactFlow,
  Controls,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeTypes,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Box } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { FlowNodeData } from '../types';
import type { NodeTemplate } from '../types';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import { FlowNode } from './flow-node';
import CustomEdge from './custom-edge';
import { handleFlowCanvasDrop } from './canvas-drop-handler';
import { FLOW_EDGE } from '../flow-theme';

function CanvasControlsInner() {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const { t } = useTranslation();
  return (
    <Controls showZoom={false} showFitView={false} showInteractive={false}>
      <button
        type="button"
        className="react-flow__controls-button"
        onClick={() => zoomIn()}
        aria-label={t('agentBuilder.zoomInAria')}
      >
        <MaterialIcon name="add" size={20} color="currentColor" />
      </button>
      <button
        type="button"
        className="react-flow__controls-button"
        onClick={() => zoomOut()}
        aria-label={t('agentBuilder.zoomOutAria')}
      >
        <MaterialIcon name="remove" size={20} color="currentColor" />
      </button>
      <button
        type="button"
        className="react-flow__controls-button"
        onClick={() => fitView({ padding: 0.28, maxZoom: 0.92 })}
        aria-label={t('agentBuilder.fitViewAria')}
      >
        <MaterialIcon name="fit_screen" size={20} color="currentColor" />
      </button>
    </Controls>
  );
}

const edgeTypes = { default: CustomEdge, smoothstep: CustomEdge };

export function AgentBuilderCanvas(props: {
  sidebarOpen: boolean;
  sidebarWidth: number;
  nodes: Node<FlowNodeData>[];
  edges: Edge[];
  onNodesChange: (changes: unknown) => void;
  onEdgesChange: (changes: unknown) => void;
  onConnect: (c: Connection) => void;
  onEdgeClick: (e: React.MouseEvent, edge: Edge) => void;
  setNodes: React.Dispatch<React.SetStateAction<Node<FlowNodeData>[]>>;
  setEdges: React.Dispatch<React.SetStateAction<Edge[]>>;
  nodeTemplates: NodeTemplate[];
  configuredConnectors: Connector[];
  activeAgentConnectors: Connector[];
  onNodeDelete: (id: string) => void;
  onError?: (msg: string) => void;
  readOnly?: boolean;
}) {
  const {
    sidebarOpen,
    sidebarWidth,
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    onConnect,
    onEdgeClick,
    setNodes,
    setEdges,
    nodeTemplates,
    configuredConnectors,
    activeAgentConnectors,
    onNodeDelete,
    onError,
    readOnly,
  } = props;

  const { t } = useTranslation();
  const rfRef = useRef<ReactFlowInstance<Node<FlowNodeData>> | null>(null);

  const onDeleteRef = useRef(onNodeDelete);
  useEffect(() => {
    onDeleteRef.current = onNodeDelete;
  }, [onNodeDelete]);

  // nodeTypes must be stable — changing it causes React Flow to remount all nodes.
  // Keep a stable identity for `onNodeDelete` via a ref; `readOnly` is included in deps
  // so toggling view mode updates without reading refs during render.
  const nodeTypes = useMemo<NodeTypes>(
    () => ({
      flowNode: (p) => (
        <FlowNode
          {...p}
          onDelete={(id) => onDeleteRef.current?.(id)}
          readOnly={readOnly}
        />
      ),
    }),
    [readOnly]
  );

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      if (readOnly) return;
      const inst = rfRef.current;
      if (!inst) return;
      const flowPointer = inst.screenToFlowPosition({
        x: e.clientX,
        y: e.clientY,
      });
      handleFlowCanvasDrop(e, {
        flowPointer,
        nodes,
        setNodes,
        setEdges,
        nodeTemplates,
        configuredConnectors,
        activeAgentConnectors,
        readOnly: Boolean(readOnly),
        t,
        onError,
      });
    },
    [
      activeAgentConnectors,
      configuredConnectors,
      nodeTemplates,
      nodes,
      onError,
      readOnly,
      setEdges,
      setNodes,
      t,
    ]
  );

  return (
    <Box
      style={{
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        position: 'relative',
      }}
      className="agent-builder-flow"
    >
      <ReactFlow
        onInit={(instance) => {
          rfRef.current = instance;
        }}
        nodes={nodes}
        edges={edges}
        onNodesChange={readOnly ? undefined : onNodesChange}
        onEdgesChange={readOnly ? undefined : onEdgesChange}
        onConnect={readOnly ? undefined : onConnect}
        onDrop={readOnly ? undefined : onDrop}
        onDragOver={readOnly ? undefined : onDragOver}
        onEdgeClick={readOnly ? undefined : onEdgeClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{
          padding: 0.28,
          minZoom: 0.25,
          maxZoom: 0.92,
        }}
        defaultViewport={{ x: 0, y: 0, zoom: 0.52 }}
        minZoom={0.25}
        maxZoom={2}
        snapToGrid
        snapGrid={[20, 20]}
        defaultEdgeOptions={{
          style: { strokeWidth: 1.5, stroke: FLOW_EDGE.line },
          type: 'smoothstep',
          animated: false,
        }}
        style={{ width: '100%', height: '100%' }}
        panOnScroll
        selectionOnDrag={!readOnly}
        panOnDrag={readOnly ? true : [1, 2]}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        proOptions={{ hideAttribution: true }}
      >
        <Background
          id="agent-builder-dots"
          gap={20}
          size={4}
          color="var(--agent-flow-canvas-dot)"
          bgColor="var(--agent-flow-canvas-bg)"
        />
        <CanvasControlsInner />
      </ReactFlow>
    </Box>
  );
}
