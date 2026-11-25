'use client';

import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Background,
  ReactFlow,
  Controls,
  useReactFlow,
  type FitViewOptions,
  type Connection,
  type Edge,
  type Node,
  type NodeTypes,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Box, Flex } from '@radix-ui/themes';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { FlowNodeData } from '../types';
import type { NodeTemplate } from '../types';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import { FlowNode } from './flow-node';
import CustomEdge from './custom-edge';
import { addNodeAtViewportCenter, handleFlowCanvasDrop } from './canvas-drop-handler';
import { InitialFlowFraming } from './initial-flow-framing';
import { useCanHover } from '@/lib/hooks/use-can-hover';
import { useIsBelowDesktop, useIsMobile } from '@/lib/hooks/use-is-mobile';
import { FLOW_EDGE } from '../flow-theme';
import { useThemeAppearance } from '@/app/components/theme-provider';

/** The fit-view control: the whole flow, however small (the editor opens with `InitialFlowFraming`). */
const AGENT_BUILDER_FLOW_FIT = {
  padding: 0.08,
  minZoom: 0.25,
  maxZoom: 1.38,
  duration: 0,
} as const;

/** A phone is too narrow for the default framing: allow a smaller zoom so every node fits whole. */
const AGENT_BUILDER_FLOW_FIT_MOBILE = { ...AGENT_BUILDER_FLOW_FIT, padding: 0.12, minZoom: 0.1 } as const;
const flowFit = (isMobile: boolean) => (isMobile ? AGENT_BUILDER_FLOW_FIT_MOBILE : AGENT_BUILDER_FLOW_FIT);

/** Fit options; `insetLeft` keeps the framed nodes clear of a palette floating over the canvas. */
function fitOptions(isMobile: boolean, insetLeft: number): FitViewOptions<Node<FlowNodeData>> {
  const fit = flowFit(isMobile);
  if (insetLeft <= 0) return { ...fit };
  return {
    ...fit,
    padding: { top: fit.padding, right: fit.padding, bottom: fit.padding, left: `${Math.round(insetLeft)}px` },
  };
}

function CanvasControlsInner({ insetLeft }: { insetLeft: number }) {
  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const { t } = useTranslation();
  const isMobile = useIsMobile();
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
        onClick={() => fitView(fitOptions(isMobile, insetLeft))}
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
  mcpDropBlockedMessage?: string;
  /** Filled with a function that adds a palette row's drag payload at the canvas centre (tap-to-add). */
  tapAddRef?: React.MutableRefObject<((data: Record<string, string>) => void) | null>;
  /** Set while an existing agent loads: a loader covers the empty canvas. */
  loadingLabel?: string;
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
    mcpDropBlockedMessage,
    tapAddRef,
    loadingLabel,
  } = props;

  const { t } = useTranslation();
  const { appearance } = useThemeAppearance();
  const rfRef = useRef<ReactFlowInstance<Node<FlowNodeData>> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  // Touch screens pan with one finger; a mouse keeps drag-to-select.
  const canHover = useCanHover();
  const isMobile = useIsMobile();
  const isBelowDesktop = useIsBelowDesktop();
  // On tablets the open palette floats over the canvas's left edge (see agent-builder.tsx).
  const paletteInset =
    sidebarOpen && isBelowDesktop && !isMobile && typeof window !== 'undefined'
      ? Math.min(sidebarWidth, window.innerWidth * 0.85) + 16
      : 0;

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
        mcpDropBlockedMessage,
      });
    },
    [
      activeAgentConnectors,
      configuredConnectors,
      mcpDropBlockedMessage,
      nodeTemplates,
      nodes,
      onError,
      readOnly,
      setEdges,
      setNodes,
      t,
    ]
  );

  useEffect(() => {
    if (!tapAddRef) return;
    tapAddRef.current = (data) => {
      const inst = rfRef.current;
      const el = containerRef.current;
      if (readOnly || !inst || !el) return;
      const next = addNodeAtViewportCenter({
        data,
        reactFlow: inst,
        canvas: el,
        nodes,
        edges,
        drop: {
          nodeTemplates,
          configuredConnectors,
          activeAgentConnectors,
          readOnly: Boolean(readOnly),
          t,
          onError,
          mcpDropBlockedMessage,
        },
      });
      setNodes(next.nodes);
      setEdges(next.edges);
    };
    return () => {
      tapAddRef.current = null;
    };
  }, [
    activeAgentConnectors,
    configuredConnectors,
    edges,
    mcpDropBlockedMessage,
    nodeTemplates,
    nodes,
    onError,
    readOnly,
    setEdges,
    setNodes,
    t,
    tapAddRef,
  ]);
  return (
    <Box
      ref={containerRef}
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
        defaultViewport={{ x: 0, y: 0, zoom: 0.88 }}
        minZoom={flowFit(isMobile).minZoom}
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
        selectionOnDrag={!readOnly && canHover}
        panOnDrag={readOnly || !canHover ? true : [1, 2]}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        proOptions={{ hideAttribution: true }}
        // React Flow puts its colour mode on its root as a class, `light` by default, and Radix Themes redeclares
        // every colour scale on `.light`: left at the default, the whole canvas reads light tokens in dark mode.
        colorMode={appearance}
      >
        <Background
          id="agent-builder-dots"
          gap={20}
          size={4}
          color="var(--agent-flow-canvas-dot)"
          bgColor="var(--agent-flow-canvas-bg)"
        />
        <CanvasControlsInner insetLeft={paletteInset} />
        <InitialFlowFraming insetLeft={paletteInset} />
      </ReactFlow>
      {loadingLabel ? (
        <Flex
          role="status"
          aria-label={loadingLabel}
          align="center"
          justify="center"
          style={{ position: 'absolute', inset: 0, zIndex: 5, background: 'var(--agent-flow-canvas-bg)' }}
        >
          <LottieLoader variant="loader" size={48} showLabel label={loadingLabel} />
        </Flex>
      ) : null}
    </Box>
  );
}
