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
  Portal,
} from '@mui/material';

import { RegistryToolset, RegistryTool } from 'src/types/agent';
import ToolsetApiService from 'src/services/toolset-api';
import { useAdmin } from 'src/context/AdminContext';

import { SidebarCategory } from './sidebar-category';
import { SidebarNodeItem } from './sidebar-node-item';
import { getToolIcon, UI_ICONS } from './sidebar.icons';

interface SidebarToolsetsSectionProps {
  expandedApps: Record<string, boolean>;
  onAppToggle: (key: string) => void;
  toolsets: any[]; // Pre-loaded toolsets with status (isConfigured, isAuthenticated)
  refreshToolsets: () => Promise<void>; // Refresh toolsets after OAuth authentication
  loading: boolean; // Loading state from parent
  isBusiness?: boolean;
  activeToolsetTypes?: string[];
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

const normalizeToolsetTypeKey = (value: string): string =>
  (value || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[_-]+/g, '');

export const SidebarToolsetsSection: React.FC<SidebarToolsetsSectionProps> = ({
  expandedApps,
  onAppToggle,
  toolsets: toolsetsProp,
  refreshToolsets,
  loading: loadingProp,
  isBusiness,
  activeToolsetTypes = [],
}) => {
  const theme = useTheme();
  const { isAdmin } = useAdmin();
  const [searchQuery, setSearchQuery] = useState('');
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string }>({
    open: false,
    message: '',
  });

  // Use toolsets from props (already loaded with status)
  const toolsets = toolsetsProp as ToolsetWithStatus[];
  const loading = loadingProp;
  const normalizedActiveToolsetTypes = activeToolsetTypes.map(normalizeToolsetTypeKey);

  // Track OAuth window and polling interval
  const oauthWindowRef = useRef<Window | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Use body as snackbar container to escape any local stacking context (drawers, sticky headers)
  const snackbarContainer = typeof window !== 'undefined' ? document.body : undefined;

  // Listen for OAuth completion via postMessage and refresh toolsets
  useEffect(() => {
    const handleOAuthMessage = async (event: MessageEvent) => {
      // Only act on explicit oauth-success messages to avoid false positives
      if (event.data?.type === 'oauth-success') {
        console.log('✅ OAuth authentication completed via postMessage, refreshing toolsets...');

        // Clean up any existing poll since the popup already reported success
        if (pollIntervalRef.current) {
          clearInterval(pollIntervalRef.current);
          pollIntervalRef.current = null;
        }
        oauthWindowRef.current = null;

        await refreshToolsets();

        setSnackbar({
          open: true,
          message: 'Authentication successful! Toolset is now ready to use.',
        });
      }
    };

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

  // Show empty state only when not loading and no results after filter
  if (!loading && filteredToolsets.length === 0) {
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
    const isFromRegistry = (toolset as any).isFromRegistry === true || !instanceId;

    // If this is a synthetic/registry-only entry, route admins to Available tab
    // and prompt non-admins to contact their administrator.
    if (isFromRegistry) {
      if (isAdmin) {
        const basePath = isBusiness ? '/account/company-settings/settings/toolsets' : '/account/individual/settings/toolsets';
        window.location.href = `${basePath}?tab=available`;
        return;
      }
      setSnackbar({
        open: true,
        message: `${toolset.displayName} is not configured yet. Please contact your administrator to configure it.`,
      });
      return;
    }

    if (authType === 'OAUTH') {
      try {
        const result = await ToolsetApiService.getInstanceOAuthAuthorizationUrl(instanceId);

        if (!result.success || !result.authorizationUrl) {
          setSnackbar({
            open: true,
            message: 'Failed to start OAuth authentication. Please try again.',
          });
          return;
        }

        // Open the OAuth popup
        const width = 600;
        const height = 700;
        const left = window.screen.width / 2 - width / 2;
        const top = window.screen.height / 2 - height / 2;
        const popup = window.open(
          result.authorizationUrl,
          'oauth_popup',
          `width=${width},height=${height},left=${left},top=${top},scrollbars=yes,resizable=yes`
        );

        if (!popup) {
          setSnackbar({
            open: true,
            message: 'Popup blocked. Please allow popups for this site and try again.',
          });
          return;
        }

        popup.focus();
        oauthWindowRef.current = popup;

        // ── Poll exclusively for popup closure ──
        // Do NOT call refreshToolsets until popup.closed is confirmed true.
        // This prevents stale-status refreshes while OAuth is still in progress.
        let statusChecked = false;

        // Clean up any pre-existing interval before starting a new one
        if (pollIntervalRef.current) {
          clearInterval(pollIntervalRef.current);
        }

        pollIntervalRef.current = setInterval(async () => {
          // Only act once the popup has genuinely closed
          if (!popup.closed || statusChecked) return;

          // Popup is confirmed closed — mark immediately so subsequent ticks are no-ops
          statusChecked = true;
          clearInterval(pollIntervalRef.current!);
          pollIntervalRef.current = null;
          oauthWindowRef.current = null;

          // Give the backend a moment to finish processing the OAuth callback
          // before refreshing, so the auth status is up-to-date.
          await new Promise((resolve) => setTimeout(resolve, 1500));

          console.log('OAuth window closed, refreshing toolsets...');
          await refreshToolsets();
        }, 500); // Poll at 500 ms for a snappier response after popup closes
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
          const normalizedToolsetType = normalizeToolsetTypeKey(
            (toolset as any).toolsetType || toolset.name || ''
          );
          const hasTypeAlreadyInFlow = normalizedActiveToolsetTypes.includes(normalizedToolsetType);
          const isFromRegistry = (toolset as any).isFromRegistry === true || !(toolset as any).instanceId;
          // Tooltip for configure action (support multi-line)
          const configureTooltip = isFromRegistry
            ? (<><span>Not configured (registry).</span><br /><span>Admins can create an instance.</span></>)
            : needsConfiguration && toolset.isConfigured
              ? 'Authenticate this toolset'
              : 'Configure toolset';
          
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
            if (isFromRegistry) {
              setSnackbar({
                open: true,
                message: `${toolset.displayName} is not configured yet. Please contact your administrator to configure it.`,
              });
              return;
            }
            const reason = !toolset.isConfigured ? 'not configured' : 'not authenticated';
            setSnackbar({
              open: true,
              message: `${toolset.displayName} is ${reason}. Please configure it before using.`,
            });
          };

          const handleDuplicateTypeDragAttempt = () => {
            setSnackbar({
              open: true,
              message: `Only one ${formatToolsetTypeLabel((toolset as any).toolsetType || toolset.name)} instance can be added to the flow at a time.`,
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
              dragType={
                needsConfiguration || hasTypeAlreadyInFlow
                  ? undefined
                  : `toolset-${toolset.name.toLowerCase()}`
              }
              dragData={needsConfiguration || hasTypeAlreadyInFlow ? undefined : toolsetDragData}
              borderColor={theme.palette.divider}
              showConfigureIcon={needsConfiguration}
              showAuthenticatedIndicator={!needsConfiguration && toolset.isAuthenticated}
              onConfigureClick={needsConfiguration ? () => handleConfigureClick(toolset) : undefined}
              configureTooltip={configureTooltip}
              configureIcon={isFromRegistry ?  UI_ICONS.alertCircle: UI_ICONS.settings}
              configureIconColor={isFromRegistry ? theme.palette.error.main : theme.palette.warning.main}
              onDragAttempt={
                hasTypeAlreadyInFlow
                  ? handleDuplicateTypeDragAttempt
                  : needsConfiguration
                  ? handleUnconfiguredDragAttempt
                  : undefined
              }
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
                  const normalizedToolsetType = normalizeToolsetTypeKey(
                    (toolset as any).toolsetType || toolset.name || ''
                  );
                  const hasTypeAlreadyInFlow = normalizedActiveToolsetTypes.includes(normalizedToolsetType);
                  const isFromRegistry = (toolset as any).isFromRegistry === true || !instanceId;

                  const configureTooltip = isFromRegistry
                    ? (<><span>Not configured (registry).</span><br /><span>Admins can create an instance.</span></>)
                    : needsConfiguration && toolset.isConfigured
                      ? 'Authenticate this toolset'
                      : 'Configure toolset';

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
                    if (isFromRegistry) {
                      setSnackbar({
                        open: true,
                        message: `${instanceName} is not configured yet. Please contact your administrator to configure it.`,
                      });
                      return;
                    }
                    const reason = !toolset.isConfigured ? 'not configured' : 'not authenticated';
                    setSnackbar({
                      open: true,
                      message: `${instanceName} is ${reason}. Please configure it before using.`,
                    });
                  };

                  const handleDuplicateTypeDragAttempt = () => {
                    setSnackbar({
                      open: true,
                      message: `Only one ${formatToolsetTypeLabel((toolset as any).toolsetType || toolset.name)} instance can be added to the flow at a time.`,
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
                      dragType={
                        needsConfiguration || hasTypeAlreadyInFlow
                          ? undefined
                          : `toolset-${instanceId}`
                      }
                      dragData={needsConfiguration || hasTypeAlreadyInFlow ? undefined : toolsetDragData}
                      borderColor={theme.palette.divider}
                      showConfigureIcon={needsConfiguration}
                      showAuthenticatedIndicator={!needsConfiguration && toolset.isAuthenticated}
                      onConfigureClick={needsConfiguration ? () => handleConfigureClick(toolset) : undefined}
                      configureTooltip={configureTooltip}
                      configureIcon={isFromRegistry ? UI_ICONS.connector : UI_ICONS.alertCircle}
                      configureIconColor={isFromRegistry ? theme.palette.info.main : theme.palette.warning.main}
                      onDragAttempt={
                        hasTypeAlreadyInFlow
                          ? handleDuplicateTypeDragAttempt
                          : needsConfiguration
                          ? handleUnconfiguredDragAttempt
                          : undefined
                      }
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
      <Portal container={snackbarContainer}>
        <Snackbar
          open={snackbar.open}
          autoHideDuration={5000}
          onClose={(_event, reason) => {
            // Keep snackbar visible on clickaway during drag/drop so the user notices it
            if (reason === 'clickaway') {
              return;
            }
            setSnackbar({ ...snackbar, open: false });
          }}
          anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
        >
          <Alert
            onClose={() => setSnackbar({ ...snackbar, open: false })}
            severity="warning"
            sx={{ width: '100%' }}
          >
            {snackbar.message}
          </Alert>
        </Snackbar>
      </Portal>
    </Box>
  );
};