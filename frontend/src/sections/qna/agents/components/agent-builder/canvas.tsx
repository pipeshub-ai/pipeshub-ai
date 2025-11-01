// src/sections/agents/components/flow-builder-canvas.tsx
import React, { useRef, useCallback } from 'react';
import {
  ReactFlow,
  MiniMap,
  Controls,
  Background,
  BackgroundVariant,
  Node,
  Edge,
  Connection,
  NodeTypes,
  Panel,
  useReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Box,
  Paper,
  Typography,
  Stack,
  useTheme,
  alpha,
  IconButton,
  Tooltip,
} from '@mui/material';
import { Icon } from '@iconify/react';
import sparklesIcon from '@iconify-icons/mdi/auto-awesome';
import fitScreenIcon from '@iconify-icons/mdi/fit-to-screen';
import centerFocusIcon from '@iconify-icons/mdi/focus-auto';
import zoomInIcon from '@iconify-icons/mdi/plus';
import zoomOutIcon from '@iconify-icons/mdi/minus';
import playIcon from '@iconify-icons/mdi/play';

// Import the enhanced FlowNode component
import FlowNode from './flow-node';
import { normalizeDisplayName } from '../../utils/agent';
 
interface FlowNodeData extends Record<string, unknown> {
  id: string;
  type: string;
  label: string;
  config: Record<string, any>;
  description?: string;
  icon?: any;
  inputs?: string[];
  outputs?: string[];
  isConfigured?: boolean;
}

interface NodeTemplate {
  type: string;
  label: string;
  description: string;
  icon: any;
  defaultConfig: Record<string, any>;
  inputs: string[];
  outputs: string[];
  category: 'inputs' | 'llm' | 'tools' | 'knowledge' | 'outputs' | 'agent';
}

interface FlowBuilderCanvasProps {
  nodes: Node<FlowNodeData>[];
  edges: Edge[];
  onNodesChange: (changes: any) => void;
  onEdgesChange: (changes: any) => void;
  onConnect: (connection: Connection) => void;
  onNodeClick: (event: React.MouseEvent, node: any) => void;
  onEdgeClick: (event: React.MouseEvent, edge: Edge<any>) => void;
  nodeTemplates: NodeTemplate[];
  onDrop: (event: React.DragEvent) => void;
  onDragOver: (event: React.DragEvent) => void;
  setNodes: React.Dispatch<React.SetStateAction<Node<FlowNodeData>[]>>;
  sidebarOpen: boolean;
  sidebarWidth: number;
  onNodeEdit?: (nodeId: string, data: any) => void;
  onNodeDelete?: (nodeId: string) => void;
}

// Enhanced Controls Component that uses ReactFlow context
const EnhancedControls: React.FC<{ colors: any }> = ({ colors }) => {
  const { fitView, zoomIn, zoomOut } = useReactFlow();

  return (
    <Controls
      style={{
        background: colors.background.paper,
        border: `1px solid ${colors.border.main}`,
        borderRadius: 12,
        boxShadow: colors.isDark 
          ? `0 8px 32px rgba(0, 0, 0, 0.4), 0 4px 16px rgba(0, 0, 0, 0.2)`
          : `0 8px 32px rgba(15, 23, 42, 0.08), 0 4px 16px rgba(15, 23, 42, 0.04)`,
        backdropFilter: 'blur(10px)',
        padding: '4px',
      }}
      showZoom={false}
      showFitView={false}
      showInteractive={false}
    >
      <Tooltip title="Zoom In" placement="top">
        <IconButton
          size="small"
          onClick={() => zoomIn()}
          sx={{
            width: 32,
            height: 32,
            margin: '2px',
            backgroundColor: 'transparent',
            color: colors.text.secondary,
            borderRadius: 1.5,
            transition: 'all 0.2s ease',
            '&:hover': {
              backgroundColor: alpha(colors.primary, 0.1),
              color: colors.primary,
              transform: 'scale(1.1)',
            },
          }}
        >
          <Icon icon={zoomInIcon} width={16} height={16} />
        </IconButton>
      </Tooltip>
      
      <Tooltip title="Zoom Out" placement="top">
        <IconButton
          size="small"
          onClick={() => zoomOut()}
          sx={{
            width: 32,
            height: 32,
            margin: '2px',
            backgroundColor: 'transparent',
            color: colors.text.secondary,
            borderRadius: 1.5,
            transition: 'all 0.2s ease',
            '&:hover': {
              backgroundColor: alpha(colors.primary, 0.1),
              color: colors.primary,
              transform: 'scale(1.1)',
            },
          }}
        >
          <Icon icon={zoomOutIcon} width={16} height={16} />
        </IconButton>
      </Tooltip>

      <Tooltip title="Fit View" placement="top">
        <IconButton
          size="small"
          onClick={() => fitView({ padding: 0.2 })}
          sx={{
            width: 32,
            height: 32,
            margin: '2px',
            backgroundColor: 'transparent',
            color: colors.text.secondary,
            borderRadius: 1.5,
            transition: 'all 0.2s ease',
            '&:hover': {
              backgroundColor: alpha(colors.primary, 0.1),
              color: colors.primary,
              transform: 'scale(1.1)',
            },
          }}
        >
          <Icon icon={fitScreenIcon} width={16} height={16} />
        </IconButton>
      </Tooltip>

      <Tooltip title="Center View" placement="top">
        <IconButton
          size="small"
          onClick={() => fitView({ padding: 0.1, includeHiddenNodes: false })}
          sx={{
            width: 32,
            height: 32,
            margin: '2px',
            backgroundColor: 'transparent',
            color: colors.text.secondary,
            borderRadius: 1.5,
            transition: 'all 0.2s ease',
            '&:hover': {
              backgroundColor: alpha(colors.primary, 0.1),
              color: colors.primary,
              transform: 'scale(1.1)',
            },
          }}
        >
          <Icon icon={centerFocusIcon} width={16} height={16} />
        </IconButton>
      </Tooltip>
    </Controls>
  );
};

const AgentBuilderCanvas: React.FC<FlowBuilderCanvasProps> = ({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onNodeClick,
  onEdgeClick,
  nodeTemplates,
  onDrop,
  onDragOver,
  setNodes,
  sidebarOpen,
  sidebarWidth,
  onNodeEdit,
  onNodeDelete,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const reactFlowWrapper = useRef<HTMLDivElement>(null);

  // Professional color scheme
  const colors = {
    primary: isDark ? '#6366f1' : '#4f46e5',
    secondary: isDark ? '#8b5cf6' : '#7c3aed',
    success: isDark ? '#10b981' : '#059669',
    background: {
      main: isDark ? '#1a1a1a' : '#fafbfc', // Lighter dark background for better node visibility
      paper: isDark ? '#262626' : '#ffffff', // Adjusted paper color
      elevated: isDark ? '#333333' : '#f8fafc',
    },
    border: {
      main: isDark ? '#404040' : '#e2e8f0',
      subtle: isDark ? '#2a2a2a' : '#f1f5f9',
    },
    text: {
      primary: isDark ? '#f8fafc' : '#0f172a',
      secondary: isDark ? '#cbd5e1' : '#64748b',
    },
    isDark,
  };

  const FlowNodeWrapper = useCallback((props: any) => <FlowNode {...props} />, []);

  const nodeTypes: NodeTypes = {
    flowNode: FlowNodeWrapper,
  };

  const handleDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      if (!reactFlowWrapper.current) return;

      const reactFlowBounds = reactFlowWrapper.current.getBoundingClientRect();
      const type = event.dataTransfer.getData('application/reactflow');
      const template = nodeTemplates.find((t) => t.type === type);

      if (!template) return;

      const position = {
        x: event.clientX - reactFlowBounds.left - 130,
        y: event.clientY - reactFlowBounds.top - 40,
      };

      const newNode: Node<FlowNodeData> = {
        id: `${type}-${Date.now()}`,
        type: 'flowNode',
        position,
        data: {
          id: `${type}-${Date.now()}`,
          type: template.type,
          label: normalizeDisplayName(template.label),
          description: template.description,
          icon: template.icon,
          config: {
            ...template.defaultConfig,
            // Add default approval config for tool nodes
            ...(template.type.startsWith('tool-') && !template.type.startsWith('tool-group-') && {
              approvalConfig: {
                requiresApproval: false,
                approvers: { users: [], groups: [] },
                approvalThreshold: 'single',
                autoApprove: false,
              }
            })
          },
          inputs: template.inputs,
          outputs: template.outputs,
          isConfigured: false,
        },
      };

      setNodes((nds) => [...nds, newNode]);
    },
    [setNodes, nodeTemplates]
  );

  return (
    <Box
      sx={{
        flexGrow: 1,
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        transition: theme.transitions.create(['width'], {
          easing: theme.transitions.easing.sharp,
          duration: theme.transitions.duration.leavingScreen,
        }),
        width: sidebarOpen ? `calc(100% - ${sidebarWidth}px)` : '100%',
        height: '100%',
        // backgroundColor: colors.background.main,
      }}
    >
      {/* Enhanced React Flow Canvas */}
      <Box
        ref={reactFlowWrapper}
        sx={{
          flexGrow: 1,
          width: '100%',
          height: '100%',
          minHeight: 0,
          position: 'relative',
          '& .react-flow__renderer': {
            filter: isDark ? 'contrast(1.05) brightness(1.1)' : 'none', // Adjusted brightness for better visibility
          },
          '& .react-flow__controls': {
            bottom: 20,
            left: 20,
            zIndex: 10,
          },
          '& .react-flow__minimap': {
            bottom: 20,
            right: 20,
            zIndex: 10,
          },
          '& .react-flow__background': {
            opacity: isDark ? 0.2 : 0.5, // Reduced opacity in dark mode for subtler background
          },
          // Enhanced edge styling
          '& .react-flow__edge-path': {
            strokeWidth: 2,
            filter: 'drop-shadow(0 1px 2px rgba(0, 0, 0, 0.1))',
          },
          '& .react-flow__edge.selected .react-flow__edge-path': {
            strokeWidth: 3,
            filter: `drop-shadow(0 2px 4px ${alpha(colors.primary, 0.3)})`,
          },
          // Enhanced connection line
          '& .react-flow__connectionline': {
            strokeWidth: 2,
            strokeDasharray: '5,5',
            stroke: colors.primary,
            filter: `drop-shadow(0 2px 4px ${alpha(colors.primary, 0.3)})`,
          },
        }}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onDrop={handleDrop}
          onDragOver={onDragOver}
          onNodeClick={onNodeClick}
          onEdgeClick={onEdgeClick}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{
            padding: 0.1,
            includeHiddenNodes: false,
            minZoom: 0.4,
            maxZoom: 1.5,
          }}
          defaultViewport={{ x: 0, y: 0, zoom: 0.6 }}
          minZoom={0.3}
          maxZoom={2.0}
          snapToGrid
          snapGrid={[25, 25]}
          defaultEdgeOptions={{
            style: {
              strokeWidth: 3,
              stroke: colors.primary,
              cursor: 'pointer',
              filter: 'drop-shadow(0 2px 4px rgba(0, 0, 0, 0.15))',
            },
            type: 'smoothstep',
            animated: false,
            interactionWidth: 30,
            // Remove arrowheads for cleaner tree appearance
          }}
          style={{
            // backgroundColor: colors.background.main,
            width: '100%',
            height: '100%',
          }}
          panOnScroll
          selectionOnDrag
          panOnDrag={[1, 2]}
          selectNodesOnDrag={false}
          proOptions={{ hideAttribution: true }}
        >
          {/* Enhanced Controls */}
          <EnhancedControls colors={colors} />

          {/* Enhanced Background */}
          <Background 
            variant={BackgroundVariant.Dots} 
            size={2}
            gap={20}
            style={{
              opacity: isDark ? 0.3 : 0.5,
            }}
          />


          {/* Status Panel */}
          <Panel position="top-left">
            <Paper
              sx={{
                px: 2,
                py: 1,
                backdropFilter: 'blur(10px)',
                border: `1px solid ${colors.border.main}`,
                borderRadius: 2,
                boxShadow: isDark 
                  ? `0 4px 16px rgba(0, 0, 0, 0.3)`
                  : `0 4px 16px rgba(15, 23, 42, 0.06)`,
                display: 'flex',
                alignItems: 'center',
                gap: 1.5,
              }}
            >
              <Box
                sx={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  backgroundColor: colors.success,
                  boxShadow: `0 0 8px ${alpha(colors.success, 0.6)}`,
                }}
              />
              <Typography
                variant="caption"
                sx={{
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  color: colors.text.primary,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                }}
              >
                Agent Builder
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  fontSize: '0.7rem',
                  color: colors.text.secondary,
                  ml: 0.5,
                }}
              >
                {nodes.length} nodes, {edges.length} connections
              </Typography>
            </Paper>
          </Panel>
        </ReactFlow>
      </Box>
    </Box>
  );
};

export default AgentBuilderCanvas;