// src/sections/agents/components/flow-builder-canvas.tsx
import React, { useRef, useCallback, useMemo, memo, useEffect, useState } from 'react';
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
import CustomEdge from './custom-edge';
import { normalizeDisplayName } from '../../utils/agent';
import type { AgentBuilderError, NodeTemplate } from '../../types/agent';

// Define nodeTypes OUTSIDE component to prevent re-creation
// This is critical for ReactFlow performance
const createNodeTypes = (onDelete?: (nodeId: string) => void): NodeTypes => ({
  flowNode: (props: any) => <FlowNode {...props} onDelete={onDelete} />,
});

// Define edgeTypes for custom edge styling
const edgeTypes = {
  default: CustomEdge,
  smoothstep: CustomEdge,
};
 
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
  configuredConnectors?: any[];
  activeAgentConnectors?: any[];
  onNodeEdit?: (nodeId: string, data: any) => void;
  onNodeDelete?: (nodeId: string) => void;
  onError?: (error: string | import('../../types/agent').AgentBuilderError) => void;
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
            width: 28,
            height: 28,
            margin: '1px',
            backgroundColor: 'transparent',
            color: colors.text.secondary,
            borderRadius: 0.5,
            transition: 'all 0.15s ease',
            '&:hover': {
              backgroundColor: colors.background.elevated,
              color: colors.text.primary,
            },
          }}
        >
          <Icon icon={zoomInIcon} width={14} height={14} />
        </IconButton>
      </Tooltip>
      
      <Tooltip title="Zoom Out" placement="top">
        <IconButton
          size="small"
          onClick={() => zoomOut()}
          sx={{
            width: 28,
            height: 28,
            margin: '1px',
            backgroundColor: 'transparent',
            color: colors.text.secondary,
            borderRadius: 0.5,
            transition: 'all 0.15s ease',
            '&:hover': {
              backgroundColor: colors.background.elevated,
              color: colors.text.primary,
            },
          }}
        >
          <Icon icon={zoomOutIcon} width={14} height={14} />
        </IconButton>
      </Tooltip>

      <Tooltip title="Fit View" placement="top">
        <IconButton
          size="small"
          onClick={() => fitView({ padding: 0.2 })}
          sx={{
            width: 28,
            height: 28,
            margin: '1px',
            backgroundColor: 'transparent',
            color: colors.text.secondary,
            borderRadius: 0.5,
            transition: 'all 0.15s ease',
            '&:hover': {
              backgroundColor: colors.background.elevated,
              color: colors.text.primary,
            },
          }}
        >
          <Icon icon={fitScreenIcon} width={14} height={14} />
        </IconButton>
      </Tooltip>

      <Tooltip title="Center View" placement="top">
        <IconButton
          size="small"
          onClick={() => fitView({ padding: 0.1, includeHiddenNodes: false })}
          sx={{
            width: 28,
            height: 28,
            margin: '1px',
            backgroundColor: 'transparent',
            color: colors.text.secondary,
            borderRadius: 0.5,
            transition: 'all 0.15s ease',
            '&:hover': {
              backgroundColor: colors.background.elevated,
              color: colors.text.primary,
            },
          }}
        >
          <Icon icon={centerFocusIcon} width={14} height={14} />
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
  configuredConnectors = [],
  activeAgentConnectors = [],
  onNodeEdit,
  onNodeDelete,
  onError,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const reactFlowWrapper = useRef<HTMLDivElement>(null);

  // Professional minimal color scheme - using theme colors
  const colors = useMemo(() => ({
    primary: theme.palette.text.secondary,
    secondary: theme.palette.text.secondary,
    success: theme.palette.text.secondary,
    background: {
      main: theme.palette.background.default,
      paper: theme.palette.background.paper,
      elevated: theme.palette.background.paper,
    },
    border: {
      main: theme.palette.divider,
      subtle: theme.palette.divider,
    },
    text: {
      primary: theme.palette.text.primary,
      secondary: theme.palette.text.secondary,
    },
    isDark,
  }), [isDark, theme]);

  // Use a ref to always hold the latest onNodeDelete callback, avoiding stale closures
  const onNodeDeleteRef = useRef(onNodeDelete);
  useEffect(() => {
    onNodeDeleteRef.current = onNodeDelete;
  }, [onNodeDelete]);

  // Create stable nodeTypes using a stable wrapper that reads from the ref
  const stableOnDelete = useCallback((nodeId: string) => {
    onNodeDeleteRef.current?.(nodeId);
  }, []);

  // Create stable nodeTypes object - only created once, uses stable callback via ref
  const [nodeTypes] = useState<NodeTypes>(() => createNodeTypes(stableOnDelete));

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
      
      // Extract toolset-specific drag data (check both 'type' key and other keys)
      const toolsetName = event.dataTransfer.getData('toolsetName');
      let toolsetDisplayName = event.dataTransfer.getData('displayName');
      // If displayName contains " - " (toolset - tool format), extract just the toolset name
      if (toolsetDisplayName && toolsetDisplayName.includes(' - ')) {
        toolsetDisplayName = toolsetDisplayName.split(' - ')[0].trim();
      }
      const toolsetIconPath = event.dataTransfer.getData('iconPath');
      const toolsetCategory = event.dataTransfer.getData('category');
      const toolsetType = event.dataTransfer.getData('type'); // 'toolset' or 'tool'
      const toolFullName = event.dataTransfer.getData('fullName');
      const toolName = event.dataTransfer.getData('toolName');
      const toolDescription = event.dataTransfer.getData('description');
      const isToolsetConfigured = event.dataTransfer.getData('isConfigured') === 'true';
      const isToolsetAuthenticated = event.dataTransfer.getData('isAuthenticated') === 'true';
      
      
      // Handle toolset drops (toolsets don't have templates, they're created dynamically)
      if (type.startsWith('toolset-') || toolsetType === 'toolset') {
        // Validate toolset is configured and authenticated
        if (!isToolsetConfigured || !isToolsetAuthenticated) {
          if (onError) {
            const reason = !isToolsetConfigured ? 'not configured' : 'not authenticated';
            onError(`${toolsetDisplayName || toolsetName} is ${reason}. Please configure it in settings before using.`);
          }
          return;
        }
        
        if (!allToolsStr) {
          if (onError) {
            onError('No tools found for this toolset.');
          }
          return;
        }
        
        try {
          const allTools = JSON.parse(allToolsStr);
          const selectedToolsStr = event.dataTransfer.getData('selectedTools');
          const selectedTools = selectedToolsStr ? JSON.parse(selectedToolsStr) : allTools.map((t: any) => t.toolName || t.name);
          
          // Normalize tool structure (ToolsetNode expects { name, fullName, description, toolsetName })
          const normalizeTool = (t: any) => ({
            name: t.toolName || t.name || '',
            fullName: t.fullName || `${toolsetName}.${t.toolName || t.name || ''}`,
            description: t.description || '',
            toolsetName: t.toolsetName || toolsetName,
          });
          
          const normalizedTools = allTools.map(normalizeTool);
          
          // Check if a toolset node for this toolset already exists
          const existingToolsetNode = nodes.find(
            (n) => n.data.type.startsWith('toolset-') && 
                   n.data.config?.toolsetName === toolsetName
          );
          
          if (existingToolsetNode) {
            // Merge tools into existing toolset node (add tools that don't exist)
            const existingTools = existingToolsetNode.data.config?.tools || [];
            const existingToolNames = new Set(existingTools.map((t: any) => t.fullName || t.name));
            
            const newTools = normalizedTools.filter(
              (t: any) => !existingToolNames.has(t.fullName) && !existingToolNames.has(t.name)
            );
            
            if (newTools.length > 0) {
              setNodes((nds) =>
                nds.map((node) =>
                  node.id === existingToolsetNode.id
                    ? {
                        ...node,
                        data: {
                          ...node.data,
                          config: {
                            ...node.data.config,
                            tools: [...existingTools, ...newTools],
                            selectedTools: [
                              ...(node.data.config?.selectedTools || []),
                              ...newTools.map((t: any) => t.name),
                            ],
                            // Update availableTools to include all tools
                            availableTools: normalizedTools,
                          },
                        },
                      }
                    : node
                )
              );
            }
            return;
          }
          
          // No existing toolset node, create a new one
          const position = {
            x: event.clientX - reactFlowBounds.left - 130,
            y: event.clientY - reactFlowBounds.top - 40,
          };
          
          const newNode: Node<FlowNodeData> = {
            id: `toolset-${toolsetName}-${Date.now()}`,
            type: 'flowNode',
            position,
            data: {
              id: `toolset-${toolsetName}-${Date.now()}`,
              type: `toolset-${toolsetName}`,
              label: normalizeDisplayName(toolsetDisplayName || toolsetName),
              description: `${toolsetDisplayName || toolsetName} with ${toolCount || normalizedTools.length} tools`,
              icon: toolsetIconPath || '/assets/icons/toolsets/collections-gray.svg',
              category: 'toolset',
              config: {
                toolsetName,
                displayName: toolsetDisplayName || toolsetName,
                iconPath: toolsetIconPath || '/assets/icons/toolsets/collections-gray.svg',
                category: toolsetCategory || 'app',
                tools: normalizedTools,
                availableTools: normalizedTools,
                selectedTools: selectedTools.map((t: string) => {
                  // Convert toolName to name if needed
                  const tool = normalizedTools.find((nt: any) => nt.toolName === t || nt.name === t);
                  return tool ? tool.name : t;
                }),
                isConfigured: isToolsetConfigured,
                isAuthenticated: isToolsetAuthenticated,
              },
              inputs: [],
              outputs: ['output'],
              isConfigured: true,
            },
          };
          setNodes((nds) => [...nds, newNode]);
          return;
        } catch (e) {
          console.error('Failed to parse toolset data:', e);
          if (onError) {
            onError('Failed to parse toolset data. Please try again.');
          }
          return;
        }
      }
      
      // Handle individual tool drops from toolsets - create or update toolset node
      // Check if this is a tool from a toolset (has toolsetName in drag data)
      // Construct toolFullName if it's missing
      const constructedToolFullName = toolFullName || (toolsetName && toolName ? `${toolsetName}.${toolName}` : '');
      const isToolsetTool = toolsetType === 'tool' && toolsetName && (constructedToolFullName || toolName);
      
      if (isToolsetTool) {
        // Validate toolset is configured and authenticated
        if (!isToolsetConfigured || !isToolsetAuthenticated) {
          if (onError) {
            const reason = !isToolsetConfigured ? 'not configured' : 'not authenticated';
            onError(`The "${toolsetDisplayName || toolsetName}" toolset is ${reason}. Please configure it in settings before using this tool.`);
          }
          return;
        }
        
        // Use constructed fullName
        const finalToolFullName = constructedToolFullName || `${toolsetName}.${toolName}`;
        
        // Get all tools from the toolset (from drag data)
        let allAvailableTools: any[] = [];
        if (allToolsStr) {
          try {
            allAvailableTools = JSON.parse(allToolsStr);
          } catch (e) {
            console.error('Failed to parse allTools from drag data:', e);
          }
        }
        
        // If allTools is empty, create a minimal structure with the dropped tool
        if (allAvailableTools.length === 0) {
          allAvailableTools = [{
            toolName: toolName || '',
            fullName: finalToolFullName,
            toolsetName,
            description: toolDescription || '',
            appName: toolsetName,
          }];
        }
        
        // Normalize tool structure (ToolsetNode expects { name, fullName, description, toolsetName })
        const normalizeTool = (t: any) => ({
          name: t.toolName || t.name || '',
          fullName: t.fullName || `${toolsetName}.${t.toolName || t.name || ''}`,
          description: t.description || '',
          toolsetName: t.toolsetName || toolsetName,
        });
        
        // Normalize all available tools
        const normalizedAvailableTools = allAvailableTools.map(normalizeTool);
        
        // Find the dropped tool in available tools
        const droppedTool = normalizedAvailableTools.find(
          (t: any) => t.fullName === finalToolFullName || t.name === toolName
        ) || normalizeTool({
          toolName: toolName || '',
          fullName: finalToolFullName,
          toolsetName,
          description: toolDescription || '',
        });
        
        // Check if a toolset node for this toolset already exists
        const existingToolsetNode = nodes.find(
          (n) => n.data.type.startsWith('toolset-') && 
                 n.data.config?.toolsetName === toolsetName
        );
        
        if (existingToolsetNode) {
          // Add the tool to the existing toolset node if it's not already there
          const existingTools = existingToolsetNode.data.config?.tools || [];
          const toolAlreadyExists = existingTools.some(
            (t: any) => t.fullName === droppedTool.fullName || t.name === droppedTool.name
          );
          
          if (!toolAlreadyExists) {
            setNodes((nds) =>
              nds.map((node) =>
                node.id === existingToolsetNode.id
                  ? {
                      ...node,
                      data: {
                        ...node.data,
                        config: {
                          ...node.data.config,
                          tools: [...existingTools, droppedTool],
                          selectedTools: [
                            ...(node.data.config?.selectedTools || []),
                            droppedTool.name,
                          ],
                          // Update availableTools if we have more tools now
                          availableTools: normalizedAvailableTools.length > 0 
                            ? normalizedAvailableTools 
                            : node.data.config?.availableTools || [],
                        },
                      },
                    }
                  : node
              )
            );
          }
          return;
        }
        
        // No existing toolset node, create a new one with only the dropped tool
        const position = {
          x: event.clientX - reactFlowBounds.left - 130,
          y: event.clientY - reactFlowBounds.top - 40,
        };
        
        const newNode: Node<FlowNodeData> = {
          id: `toolset-${toolsetName}-${Date.now()}`,
          type: 'flowNode',
          position,
          data: {
            id: `toolset-${toolsetName}-${Date.now()}`,
            type: `toolset-${toolsetName}`,
            label: normalizeDisplayName(toolsetDisplayName || toolsetName),
            description: `${toolsetDisplayName || toolsetName} toolset`,
            icon: toolsetIconPath || '/assets/icons/toolsets/collections-gray.svg',
            category: 'toolset',
            config: {
              toolsetName,
              displayName: toolsetDisplayName || toolsetName,
              iconPath: toolsetIconPath || '/assets/icons/toolsets/collections-gray.svg',
              category: toolsetCategory || 'app',
              // Only the dropped tool is initially selected
              tools: [droppedTool],
              // All available tools from the toolset (for adding more)
              availableTools: normalizedAvailableTools.length > 0 
                ? normalizedAvailableTools 
                : [droppedTool],
              selectedTools: [droppedTool.name],
              isConfigured: isToolsetConfigured,
              isAuthenticated: isToolsetAuthenticated,
            },
            inputs: [],
            outputs: ['output'],
            isConfigured: true,
          },
        };
        setNodes((nds) => [...nds, newNode]);
        return;
      }
      
      // For non-toolset drops, find the template

      const template = nodeTemplates.find((t) => t.type === type);
      if (!template) return;

      // Validate tools: Check if parent connector is configured and agent-active
      // Get validation data from drag event
      const isConnectorConfigured = event.dataTransfer.getData('isConfigured') === 'true';
      const isConnectorAgentActive = event.dataTransfer.getData('isAgentActive') === 'true';
      
      // Find connector from drag data - prioritize the specific connectorId from drag event
      const findConnector = (): { id: string; name: string } | null => {
        // If we have connectorId from drag data, use it directly (this is the specific instance being dragged)
        if (connectorId) {
          // Try to find the connector object to get the name, searching in both lists
          const connector = 
            configuredConnectors.find((c: any) => c._key === connectorId || (c as any).id === connectorId) ||
            activeAgentConnectors.find((c: any) => c._key === connectorId || (c as any).id === connectorId);
          
          // Always return the connectorId from drag data (the specific instance the user dragged)
          // This ensures we navigate to the correct connector instance, not just the first match
          return {
            id: connectorId, // Use the specific connectorId from drag data - this is the instance the user selected
            name: connector?.name || connectorName || connectorType || template.defaultConfig?.appName || toolAppName || 'Connector'
          };
        }
        
        // Fallback: If no connectorId in drag data, try to find by connectorType/appName
        // This should rarely happen if drag data is set correctly
        const appName = template.defaultConfig?.appName || toolAppName || connectorType;
        if (appName) {
          const connector = configuredConnectors.find((c: any) => 
            c.name?.toUpperCase() === appName.toUpperCase() || 
            c.type?.toUpperCase() === appName.toUpperCase()
          ) || activeAgentConnectors.find((c: any) => 
            c.name?.toUpperCase() === appName.toUpperCase() || 
            c.type?.toUpperCase() === appName.toUpperCase()
          );
          if (connector) {
            return { id: connector._key || (connector as any).id, name: connector.name || appName };
          }
        }
        
        return null;
      };
      
      if (template.type.startsWith('tool-') && !template.type.startsWith('tool-group-')) {
        const appName = template.defaultConfig?.appName || toolAppName;
        
        if (!isConnectorConfigured || !isConnectorAgentActive) {
          // Prevent the drop and show error with link to configure
          const connector = findConnector();
          
          if (onError) {
            const connectorDisplayName = connector?.name || appName;
            let message = '';
            let actionLink: string | undefined;
            
            if (connector) {
              if (!isConnectorConfigured) {
                message = `The "${connectorDisplayName}" connector needs to be configured first.`;
                actionLink = 'Configure Connector →';
              } else if (!isConnectorAgentActive) {
                message = `The "${connectorDisplayName}" connector needs to be enabled for agents to use this tool.`;
                actionLink = 'Enable Connector for Agents →';
              } else {
                message = `The "${connectorDisplayName}" connector needs to be configured and enabled for agents.`;
                actionLink = 'Configure & Enable Connector →';
              }
            } else {
              message = `The "${appName}" connector must be configured and enabled for agents before you can use this tool.`;
            }
            
            const errorMessage: AgentBuilderError = {
              message,
              connectorId: connector?.id,
              connectorName: connectorDisplayName,
              actionLink: connector ? actionLink : undefined,
            };
            onError(errorMessage);
          }
          
          // Return early - do not add the node to the canvas
          return;
        }
      }

      // Validate tool-group drops: Check if connector is agent-active
      if (template.type.startsWith('tool-group-')) {
        // Check if the connector instance is agent-active
        if (!isConnectorAgentActive) {
          const connector = findConnector();
          
          if (onError) {
            const connectorDisplayName = connector?.name || connectorType || template.defaultConfig?.appName || 'Connector';
            const errorMessage: import('../../types/agent').AgentBuilderError = {
              message: connector
                ? `The "${connectorDisplayName}" connector needs to be enabled for agents to use this tool group.`
                : `The connector must be enabled for agents before you can use this tool group.`,
              connectorId: connector?.id,
              connectorName: connectorDisplayName,
              actionLink: connector ? 'Enable Connector for Agents →' : undefined,
            };
            onError(errorMessage);
          }
          
          // Return early - do not add the node to the canvas
          return;
        }

        // Validate: Only one tool-group (connector instance) per connector type
        // This ensures we only have one active agent instance per type
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
    [setNodes, nodeTemplates, nodes, onError, configuredConnectors, activeAgentConnectors]
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
          edgeTypes={edgeTypes}
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
              strokeWidth: 1.5,
              stroke: isDark ? '#4a4a4a' : '#d0d0d0',
              cursor: 'pointer',
            },
            type: 'smoothstep',
            animated: false,
            interactionWidth: 30,
          }}
          style={{
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

          {/* Professional Background Pattern */}
          <Background 
            variant={BackgroundVariant.Dots} 
            size={0.8}
            gap={24}
            style={{
              opacity: isDark ? 0.3 : 0.5,
            }}
          />
          <Background 
            variant={BackgroundVariant.Lines} 
            gap={100}
            size={1}
            color={isDark ? alpha(theme.palette.text.secondary, 0.03) : alpha(theme.palette.text.secondary, 0.05)}
            style={{
              opacity: 1,
            }}
          />
        </ReactFlow>
      </Box>
    </Box>
  );
};

export default AgentBuilderCanvas;