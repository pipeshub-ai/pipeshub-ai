/**
 * SidebarToolsetsSection Component
 * Displays toolsets from in-memory registry with their tools for drag-and-drop
 * Shows configuration status and allows navigation to toolset settings for unconfigured ones
 * Similar pattern to connectors sidebar
 */

import React, { useState, useEffect, useRef } from 'react';
import {
  Box,
  List,
  Typography,
  useTheme,
  alpha,
  Snackbar,
  Alert,
} from '@mui/material';
import { Icon } from '@iconify/react';

import { RegistryToolset, RegistryTool } from 'src/types/agent';
import ToolsetApiService from 'src/services/toolset-api';

import { SidebarCategory } from './sidebar-category';
import { SidebarNodeItem } from './sidebar-node-item';
import { getToolIcon } from './sidebar.icons';

interface SidebarToolsetsSectionProps {
  expandedApps: Record<string, boolean>;
  onAppToggle: (key: string) => void;
  toolsets: any[]; // Pre-loaded toolsets with status (isConfigured, isAuthenticated)
  refreshToolsets: () => Promise<void>; // Refresh toolsets after OAuth authentication
  loading: boolean; // Loading state from parent
  isBusiness?: boolean;
}

interface ToolsetWithStatus extends RegistryToolset {
  isConfigured: boolean;
  isAuthenticated: boolean;
}

const formatToolsetTypeLabel = (toolsetTypeValue: string): string => {
  if (!toolsetTypeValue) return '';

  const normalized = toolsetTypeValue
    .trim()
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .toLowerCase()
    .replace(/\bshare\s+point\b/g, 'sharepoint');

  return normalized
    .split(' ')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
};

export const SidebarToolsetsSection: React.FC<SidebarToolsetsSectionProps> = ({
  expandedApps,
  onAppToggle,
  toolsets: toolsetsProp,
  refreshToolsets,
  loading: loadingProp,
  isBusiness,
}) => {
  const theme = useTheme();
  const [searchQuery, setSearchQuery] = useState('');
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string }>({
    open: false,
    message: '',
  });

  // Use toolsets from props (already loaded with status)
  const toolsets = toolsetsProp as ToolsetWithStatus[];
  const loading = loadingProp;

  // Track OAuth window reference
  const oauthWindowRef = useRef<Window | null>(null);
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Listen for OAuth completion and refresh toolsets
  useEffect(() => {
    const handleOAuthMessage = async (event: MessageEvent) => {
      // Check if the message is from OAuth completion
      if (event.data?.type === 'oauth-success' || event.data?.status === 'success') {
        console.log('✅ OAuth authentication completed, refreshing toolsets...');
        
        // Refresh toolsets to get updated authentication status
        await refreshToolsets();
        
        // Show success message
        setSnackbar({
          open: true,
          message: 'Authentication successful! Toolset is now ready to use.',
        });
        
        // Clean up polling if exists
        if (pollIntervalRef.current) {
          clearInterval(pollIntervalRef.current);
          pollIntervalRef.current = null;
        }
        oauthWindowRef.current = null;
      }
    };

    // Listen for messages from OAuth popup
    window.addEventListener('message', handleOAuthMessage);

    return () => {
      window.removeEventListener('message', handleOAuthMessage);
      
      // Clean up polling interval on unmount
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, [refreshToolsets]);

  const filteredToolsets = toolsets.filter((toolset) => {
    if (!searchQuery) return true;
    const query = searchQuery.toLowerCase();
    return (
      toolset.displayName.toLowerCase().includes(query) ||
      toolset.description.toLowerCase().includes(query) ||
      toolset.tools.some((tool: RegistryTool) =>
        tool.name.toLowerCase().includes(query) ||
        tool.description.toLowerCase().includes(query)
      )
    );
  });

  // Loading state is handled by parent skeleton loader
  // If toolsets are empty, show empty state
  if (toolsets.length === 0) {
    return (
      <Box sx={{ pl: 4, py: 2 }}>
        <Typography
          variant="caption"
          sx={{
            color: alpha(theme.palette.text.secondary, 0.6),
            fontSize: '0.75rem',
            fontStyle: 'italic',
          }}
        >
          No toolsets available.
        </Typography>
      </Box>
    );
  }

  // Group filtered toolsets by toolset type (similar to connector grouping)
  const toolsetsByType = filteredToolsets.reduce((acc, toolset) => {
    const toolsetType = (toolset as any).toolsetType || toolset.name || 'unknown';
    if (!acc[toolsetType]) {
      acc[toolsetType] = [];
    }
    acc[toolsetType].push(toolset);
    return acc;
  }, {} as Record<string, typeof filteredToolsets>);

  // Handle configure click based on auth type
  const handleConfigureClick = async (toolset: ToolsetWithStatus) => {
    const authType = (toolset as any).authType || '';
    const instanceId = (toolset as any).instanceId || '';
    
    if (authType === 'OAUTH') {
      // For OAuth: Call authorize API
      try {
        const result = await ToolsetApiService.getInstanceOAuthAuthorizationUrl(instanceId);
        if (result.success && result.authorizationUrl) {
          // Open OAuth window
          const width = 600;
          const height = 700;
          const left = window.screen.width / 2 - width / 2;
          const top = window.screen.height / 2 - height / 2;
          const popup = window.open(
            result.authorizationUrl,
            'oauth',
            `width=${width},height=${height},left=${left},top=${top}`
          );
          
          // Store reference to popup window
          oauthWindowRef.current = popup;
          
          // Fallback: Poll for window closure (in case postMessage doesn't work)
          pollIntervalRef.current = setInterval(async () => {
            if (!popup || popup.closed) {
              // Clean up interval
              if (pollIntervalRef.current) {
                clearInterval(pollIntervalRef.current);
                pollIntervalRef.current = null;
              }
              oauthWindowRef.current = null;
              
              // Refresh toolsets when window closes (user may have completed auth)
              console.log('OAuth window closed, refreshing toolsets...');
              await refreshToolsets();
            }
          }, 1000); // Check every second
        } else {
          setSnackbar({
            open: true,
            message: 'Failed to start OAuth authentication. Please try again.',
          });
        }
      } catch (error) {
        console.error('Error starting OAuth:', error);
        setSnackbar({
          open: true,
          message: 'Failed to start OAuth authentication. Please try again.',
        });
      }
    } else if (isBusiness) {
      // For other auth types: Navigate to toolsets page to configure
      window.location.href = '/account/company-settings/settings/toolsets';
    } else {
      window.location.href = '/account/individual/settings/toolsets';
    }
  };

  return (
    <Box sx={{ pl: 0 }}>
      {/* Toolsets Grouped by Type */}
      {Object.entries(toolsetsByType).map(([toolsetType, typeToolsets]) => {
        const isSingleInstance = typeToolsets.length === 1;
        const firstToolset = typeToolsets[0];
        const toolsetTypeKey = `toolset-type-${toolsetType}`;
        // For multiple instances, default to collapsed (false) instead of expanded (true)
        const isTypeExpanded = expandedApps[toolsetTypeKey] ?? isSingleInstance;
        
        // For single instance, render directly
        if (isSingleInstance) {
          const toolset = firstToolset;
          const toolsetKey = `toolset-${(toolset as any).instanceId || toolset.name.toLowerCase()}`;
          const isExpanded = expandedApps[toolsetKey];
          const needsConfiguration = !toolset.isConfigured || !toolset.isAuthenticated;
          
          // Create drag data for entire toolset
          const toolsetDragData = {
            type: 'toolset',
            instanceId: (toolset as any).instanceId || '',
            instanceName: (toolset as any).instanceName || toolset.displayName,
            toolsetType: (toolset as any).toolsetType || toolset.name,
            toolsetName: (toolset as any).toolsetType || toolset.name,
            displayName: toolset.displayName,
            selectedTools: JSON.stringify(toolset.tools.map((t) => t.name)),
            allTools: JSON.stringify(
              toolset.tools.map((t) => ({
                toolName: t.name,
                fullName: t.fullName || `${(toolset as any).toolsetType || toolset.name}.${t.name}`,
                toolsetName: (toolset as any).toolsetType || toolset.name,
                description: t.description,
                appName: (toolset as any).toolsetType || toolset.name,
              }))
            ),
            iconPath: toolset.iconPath || '/assets/icons/toolsets/default.svg',
            category: toolset.category || 'app',
            isConfigured: String(toolset.isConfigured),
            isAuthenticated: String(toolset.isAuthenticated),
            toolCount: String(toolset.tools.length),
          };

          // Handler for attempting to drag unconfigured toolset
          const handleUnconfiguredDragAttempt = () => {
            const reason = !toolset.isConfigured 
              ? 'not configured' 
              : 'not authenticated';
            
            setSnackbar({
              open: true,
              message: `${toolset.displayName} is ${reason}. Please configure it before using.`,
            });
          };

          // Single instance - render directly
          return (
            <SidebarCategory
              key={(toolset as any).instanceId || toolset.name.toLowerCase()}
              groupLabel={toolset.displayName}
              groupIcon={toolset.iconPath || '/assets/icons/toolsets/default.svg'}
              itemCount={toolset.tools.length}
              isExpanded={isExpanded}
              onToggle={() => onAppToggle(toolsetKey)}
              dragType={needsConfiguration ? undefined : `toolset-${toolset.name.toLowerCase()}`}
              dragData={needsConfiguration ? undefined : toolsetDragData}
              borderColor={theme.palette.divider}
              showConfigureIcon={needsConfiguration}
              showAuthenticatedIndicator={!needsConfiguration && toolset.isAuthenticated}
              onConfigureClick={needsConfiguration ? () => handleConfigureClick(toolset) : undefined}
              onDragAttempt={needsConfiguration ? handleUnconfiguredDragAttempt : undefined}
            >
              <Box
                sx={{
                  position: 'relative',
                  '&::before': {
                    content: '""',
                    position: 'absolute',
                    left: '52px',
                    top: 0,
                    bottom: 0,
                    width: '2px',
                    backgroundColor: alpha(theme.palette.divider, 0.2),
                    borderRadius: '1px',
                  },
                }}
              >
                <List dense sx={{ py: 0.5 }}>
                  {toolset.tools.map((tool: RegistryTool) => {
                    const toolDragData = {
                      type: 'tool',
                      instanceId: (toolset as any).instanceId || '',
                      instanceName: (toolset as any).instanceName || toolset.displayName,
                      toolsetType: (toolset as any).toolsetType || toolset.name,
                      toolName: tool.name,
                      fullName: tool.fullName,
                      toolsetName: (toolset as any).toolsetType || toolset.name,
                      displayName: `${toolset.displayName} - ${tool.name}`,
                      description: tool.description,
                      iconPath: toolset.iconPath,
                    };

                    return (
                      <SidebarNodeItem
                        key={tool.fullName}
                        template={{
                          type: tool.fullName,
                          label: tool.name,
                          category: 'tools',
                          description: tool.description,
                          icon: toolset.iconPath || '',
                          inputs: [],
                          outputs: [],
                          defaultConfig: {
                            ...toolDragData,
                          },
                        }}
                        isSubItem
                        sectionType="tools"
                        connectorStatus={{ 
                          isConfigured: toolset.isConfigured, 
                          isAgentActive: toolset.isAuthenticated 
                        }}
                        connectorIconPath={toolset.iconPath}
                        itemIcon={getToolIcon(tool.name, toolset.name)}
                        isDraggable={!needsConfiguration}
                      />
                    );
                  })}
                </List>
              </Box>
            </SidebarCategory>
          );
        }

        // Multiple instances - group by type
        return (
          <Box key={toolsetType} sx={{ mb: 1 }}>
            {/* Toolset Type Group Header */}
            <SidebarCategory
              key={toolsetTypeKey}
              groupLabel={formatToolsetTypeLabel((firstToolset as any).toolsetType || toolsetType)}
              groupIcon={firstToolset.iconPath || '/assets/icons/toolsets/default.svg'}
              itemCount={typeToolsets.length}
              isExpanded={isTypeExpanded}
              onToggle={() => onAppToggle(toolsetTypeKey)}
              borderColor={theme.palette.divider}
            >
              <Box sx={{ pl: 0.5 }}>
                {typeToolsets.map((toolset) => {
                  const instanceId = (toolset as any).instanceId || '';
                  const instanceName = (toolset as any).instanceName || toolset.displayName;
                  const toolsetKey = `toolset-${instanceId}`;
                  const isExpanded = expandedApps[toolsetKey];
                  const needsConfiguration = !toolset.isConfigured || !toolset.isAuthenticated;

                  // Create drag data for this instance
                  const toolsetDragData = {
                    type: 'toolset',
                    instanceId,
                    instanceName,
                    toolsetType: (toolset as any).toolsetType || toolset.name,
                    toolsetName: (toolset as any).toolsetType || toolset.name,
                    displayName: instanceName,
                    selectedTools: JSON.stringify(toolset.tools.map((t) => t.name)),
                    allTools: JSON.stringify(
                      toolset.tools.map((t) => ({
                        toolName: t.name,
                        fullName: t.fullName || `${(toolset as any).toolsetType || toolset.name}.${t.name}`,
                        toolsetName: (toolset as any).toolsetType || toolset.name,
                        description: t.description,
                        appName: (toolset as any).toolsetType || toolset.name,
                      }))
                    ),
                    iconPath: toolset.iconPath || '/assets/icons/toolsets/default.svg',
                    category: toolset.category || 'app',
                    isConfigured: String(toolset.isConfigured),
                    isAuthenticated: String(toolset.isAuthenticated),
                    toolCount: String(toolset.tools.length),
                  };

                  const handleUnconfiguredDragAttempt = () => {
                    const reason = !toolset.isConfigured 
                      ? 'not configured' 
                      : 'not authenticated';
                    
                    setSnackbar({
                      open: true,
                      message: `${instanceName} is ${reason}. Please configure it before using.`,
                    });
                  };

                  return (
                    <SidebarCategory
                      key={instanceId}
                      groupLabel={instanceName}
                      groupIcon={toolset.iconPath || '/assets/icons/toolsets/default.svg'}
                      itemCount={toolset.tools.length}
                      isExpanded={isExpanded}
                      onToggle={() => onAppToggle(toolsetKey)}
                      dragType={needsConfiguration ? undefined : `toolset-${instanceId}`}
                      dragData={needsConfiguration ? undefined : toolsetDragData}
                      borderColor={theme.palette.divider}
                      showConfigureIcon={needsConfiguration}
                      showAuthenticatedIndicator={!needsConfiguration && toolset.isAuthenticated}
                      onConfigureClick={needsConfiguration ? () => handleConfigureClick(toolset) : undefined}
                      onDragAttempt={needsConfiguration ? handleUnconfiguredDragAttempt : undefined}
                    >
                      <Box
                        sx={{
                          position: 'relative',
                          '&::before': {
                            content: '""',
                            position: 'absolute',
                            left: '32px',
                            top: 0,
                            bottom: 0,
                            width: '2px',
                            backgroundColor: alpha(theme.palette.divider, 0.2),
                            borderRadius: '1px',
                          },
                        }}
                      >
                        <List dense sx={{ py: 0.5 }}>
                          {toolset.tools.map((tool: RegistryTool) => {
                            const toolFullName = tool.fullName || `${toolset.name}.${tool.name}`;
                            const toolDragData = {
                              type: 'tool',
                              instanceId,
                              instanceName,
                              toolsetType: (toolset as any).toolsetType || toolset.name,
                              toolName: tool.name,
                              fullName: toolFullName,
                              toolsetName: (toolset as any).toolsetType || toolset.name,
                              displayName: instanceName,
                              description: tool.description,
                              iconPath: toolset.iconPath,
                              allTools: toolset.tools.map((t: RegistryTool) => ({
                                toolName: t.name,
                                fullName: t.fullName || `${(toolset as any).toolsetType || toolset.name}.${t.name}`,
                                toolsetName: (toolset as any).toolsetType || toolset.name,
                                description: t.description,
                                appName: (toolset as any).toolsetType || toolset.name,
                              })),
                              isConfigured: toolset.isConfigured,
                              isAuthenticated: toolset.isAuthenticated,
                            };

                            return (
                              <SidebarNodeItem
                                key={`${instanceId}-${toolFullName}`}
                                template={{
                                  type: toolFullName,
                                  label: tool.name,
                                  category: 'tools',
                                  description: tool.description,
                                  icon: toolset.iconPath || '',
                                  inputs: [],
                                  outputs: [],
                                  defaultConfig: {
                                    ...toolDragData,
                                  },
                                }}
                                isSubItem
                                sectionType="tools"
                                connectorStatus={{ 
                                  isConfigured: toolset.isConfigured, 
                                  isAgentActive: toolset.isAuthenticated 
                                }}
                                connectorIconPath={toolset.iconPath}
                                itemIcon={getToolIcon(tool.name, toolset.name)}
                                isDraggable={!needsConfiguration}
                              />
                            );
                          })}
                        </List>
                      </Box>
                    </SidebarCategory>
                  );
                })}
              </Box>
            </SidebarCategory>
          </Box>
        );
      })}

      {filteredToolsets.length === 0 && searchQuery && (
        <Box sx={{ pl: 4, py: 2 }}>
          <Typography
            variant="caption"
            sx={{
              color: alpha(theme.palette.text.secondary, 0.6),
              fontSize: '0.75rem',
              fontStyle: 'italic',
            }}
          >
            No toolsets or tools match &quot;{searchQuery}&quot;
          </Typography>
        </Box>
      )}

      {/* Snackbar for notifications */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={4000}
        onClose={() => setSnackbar({ ...snackbar, open: false })}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          onClose={() => setSnackbar({ ...snackbar, open: false })}
          severity="warning"
          sx={{ width: '100%' }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
};
