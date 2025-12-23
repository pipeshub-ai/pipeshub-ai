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
import type { NodeTemplate } from '../../types/agent';
 
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
  onError?: (error: string) => void;
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
  onError,
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

  const FlowNodeWrapper = useCallback((props: any) => <FlowNode {...props} onDelete={onNodeDelete} />, [onNodeDelete]);

  const nodeTypes: NodeTypes = {
    flowNode: FlowNodeWrapper,
  };

  const handleDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      if (!reactFlowWrapper.current) return;

      const reactFlowBounds = reactFlowWrapper.current.getBoundingClientRect();
      const type = event.dataTransfer.getData('application/reactflow');
      const connectorId = event.dataTransfer.getData('connectorId');
      const connectorType = event.dataTransfer.getData('connectorType');
      const connectorScope = event.dataTransfer.getData('scope');
      const toolAppName = event.dataTransfer.getData('toolAppName'); // Tool's parent app
      const connectorName = event.dataTransfer.getData('connectorName');
      const connectorIconPath = event.dataTransfer.getData('connectorIconPath'); // Connector icon
      const allToolsStr = event.dataTransfer.getData('allTools');
      const toolCount = event.dataTransfer.getData('toolCount');
      
      const template = nodeTemplates.find((t) => t.type === type);
      if (!template) return;

      // Validate tools: Check if parent connector is configured and agent-active
      // Get validation data from drag event
      const isConnectorConfigured = event.dataTransfer.getData('isConfigured') === 'true';
      const isConnectorAgentActive = event.dataTransfer.getData('isAgentActive') === 'true';
      
      if (template.type.startsWith('tool-') && !template.type.startsWith('tool-group-')) {
        const appName = template.defaultConfig?.appName || toolAppName;
        
        if (!isConnectorConfigured || !isConnectorAgentActive) {
          if (onError) {
            onError(
              `Cannot add tool "${template.label}". The ${appName} connector must be configured and agent-active first. ` +
              `Click the configure icon (⚙️) next to ${appName} in the Tools section to set it up.`
            );
          }
          return;
        }
      }

      // Validate: Only one tool-group (connector instance) per connector type
      // This ensures we only have one active agent instance per type
      if (template.type.startsWith('tool-group-')) {
        const connectorAppType = connectorType || template.defaultConfig?.appName || template.defaultConfig?.connectorType;
        if (connectorAppType) {
          // Check if a tool-group with the same connector type already exists
          const existingToolGroups = nodes.filter(
            (n) => n.data.type.startsWith('tool-group-') &&
            (n.data.config?.connectorType === connectorAppType || 
             n.data.config?.appName === connectorAppType ||
             n.data.type === template.type)
          );

          if (existingToolGroups.length > 0) {
            if (onError) {
              onError(`Only one ${connectorAppType} connector instance can be added as a Tool. Remove the existing one first.`);
            }
            return;
          }
        }
      }

      const position = {
        x: event.clientX - reactFlowBounds.left - 130,
        y: event.clientY - reactFlowBounds.top - 40,
      };

      // Handle tool-group drops (connector with all its tools)
      if (template.type.startsWith('tool-group-') && allToolsStr && connectorId) {
        try {
          const allTools = JSON.parse(allToolsStr);
          const newNode: Node<FlowNodeData> = {
            id: `${type}-${Date.now()}`,
            type: 'flowNode',
            position,
            data: {
              id: `${type}-${Date.now()}`,
              type: template.type,
              label: normalizeDisplayName(connectorName || template.label),
              description: `${connectorType} with ${toolCount} tools`,
              icon: template.icon,
              config: {
                ...template.defaultConfig,
                connectorInstanceId: connectorId,
                connectorType,
                connectorName,
                iconPath: connectorIconPath || template.defaultConfig?.iconPath, // Store connector icon for visual distinction
                tools: allTools,
                selectedTools: allTools.map((t: any) => t.toolId), // All tools selected by default
                appName: connectorType,
                appDisplayName: connectorName || connectorType,
                scope: connectorScope,
              },
              inputs: template.inputs || ['input'],
              outputs: template.outputs || ['output'],
              isConfigured: true, // Tool groups are pre-configured
            },
          };
          setNodes((nds) => [...nds, newNode]);
          return;
        } catch (e) {
          console.error('Failed to parse tools data:', e);
          return;
        }
      }

      // Handle regular node drops
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
            // Store the connector ID if this is a connector instance
            ...(connectorId && { connectorInstanceId: connectorId }),
            // Store connectorType for app nodes and tools
            ...(connectorType && { connectorType }),
            // For individual tools, store connector instance info
            ...(template.type.startsWith('tool-') && !template.type.startsWith('tool-group-') && {
              // Get connector instance info from drag data or infer from app name
              connectorInstanceId: connectorId || template.defaultConfig?.connectorInstanceId,
              connectorType: connectorType || template.defaultConfig?.appName,
              connectorName: connectorName || connectorType || template.defaultConfig?.appName,
              iconPath: connectorIconPath || template.defaultConfig?.iconPath,
              scope: connectorScope || template.defaultConfig?.scope,
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
          isConfigured: template.type.startsWith('app-') || template.type.startsWith('tool-group-'),
        },
      };

      setNodes((nds) => [...nds, newNode]);
    },
    [setNodes, nodeTemplates, nodes, onError]
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