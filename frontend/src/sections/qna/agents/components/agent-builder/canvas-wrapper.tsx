// src/sections/qna/agents/components/flow-builder-canvas-wrapper.tsx
import React, { memo } from 'react';
import { Box, useTheme, alpha } from '@mui/material';
import FlowBuilderSidebar from './sidebar';
import AgentBuilderCanvas from './canvas';
import type { AgentBuilderCanvasWrapperProps } from '../../types/agent';

const AgentBuilderCanvasWrapper: React.FC<AgentBuilderCanvasWrapperProps> = ({
  sidebarOpen,
  sidebarWidth,
  nodeTemplates,
  loading,
  activeAgentConnectors,
  configuredConnectors,
  connectorRegistry,
  toolsets,
  refreshToolsets,
  isBusiness,
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onNodeClick,
  onEdgeClick,
  onDrop,
  onDragOver,
  setNodes,
  onNodeEdit,
  onNodeDelete,
  onError,
  isReadOnly = false,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  return (
    <Box
      sx={{
        flex: 1,
        display: 'flex',
        overflow: 'hidden',
        minHeight: 0,
        backgroundColor: theme.palette.background.default,
      }}
    >
    <Box
      sx={{
        pointerEvents: isReadOnly ? 'none' : 'auto',
        opacity: isReadOnly ? 0.6 : 1,
      }}
    >
      <FlowBuilderSidebar
        sidebarOpen={sidebarOpen}
        nodeTemplates={nodeTemplates}
        loading={loading}
        sidebarWidth={sidebarWidth}
        activeAgentConnectors={activeAgentConnectors}
        configuredConnectors={configuredConnectors}
        connectorRegistry={connectorRegistry}
        toolsets={toolsets}
        refreshToolsets={refreshToolsets}
        isBusiness={isBusiness}
      />
    </Box>

    <AgentBuilderCanvas
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      onNodeClick={onNodeClick}
      onEdgeClick={onEdgeClick}
      nodeTemplates={nodeTemplates}
      onDrop={onDrop}
      onDragOver={onDragOver}
      setNodes={setNodes}
      sidebarOpen={sidebarOpen}
      sidebarWidth={sidebarWidth}
      configuredConnectors={configuredConnectors}
      activeAgentConnectors={activeAgentConnectors}
      onNodeEdit={onNodeEdit}
      onNodeDelete={onNodeDelete}
      onError={onError}
      readOnly={isReadOnly}
    />
  </Box>
  );
};

export default memo(AgentBuilderCanvasWrapper);
