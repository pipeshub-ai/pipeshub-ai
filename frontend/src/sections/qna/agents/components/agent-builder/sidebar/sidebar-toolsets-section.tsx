/**
 * SidebarToolsetsSection Component
 * Displays toolsets from in-memory registry with their tools for drag-and-drop
 * Shows configuration status and allows navigation to toolset settings for unconfigured ones
 * Similar pattern to connectors sidebar
 *
 * Search is server-side (debounced, resets to page 1).
 * Infinite scroll via IntersectionObserver loads subsequent pages.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Box,
  List,
  Typography,
  useTheme,
  alpha,
  Snackbar,
  Alert,
  Portal,
  TextField,
  InputAdornment,
  CircularProgress,
} from '@mui/material';
import { Icon } from '@iconify/react';

import { RegistryToolset, RegistryTool } from 'src/types/agent';
import ToolsetApiService from 'src/services/toolset-api';
import { useAdmin } from 'src/context/AdminContext';
import ToolsetConfigDialog from 'src/sections/toolsets/components/toolset-config-dialog';

import { SidebarCategory } from './sidebar-category';
import { SidebarNodeItem } from './sidebar-node-item';
import { getToolIcon, UI_ICONS } from './sidebar.icons';

interface SidebarToolsetsSectionProps {
  expandedApps: Record<string, boolean>;
  onAppToggle: (key: string) => void;
  toolsets: any[]; // Pre-loaded toolsets with status (isConfigured, isAuthenticated)
  refreshToolsets: (agentKey?: string, isServiceAccount?: boolean, search?: string) => Promise<void>;
  onSearch?: (query: string) => void;   // Called (debounced) when the search input changes
  onLoadMore?: () => Promise<void>;     // Called when the IntersectionObserver sentinel is visible
  hasMore?: boolean;                    // Whether there are more toolset pages to load
  loadingMore?: boolean;                // Whether a load-more fetch is in flight
  loading: boolean; // Loading state from parent
  isBusiness?: boolean;
  activeToolsetTypes?: string[];
  // Service account support
  isServiceAccount?: boolean;
  agentKey?: string;
  onManageAgentToolsetCredentials?: (toolset: any) => void;
}

interface ToolsetWithStatus extends RegistryToolset {
  isFromRegistry?: boolean;
  instanceId?: string;
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
  onSearch,
  onLoadMore,
  hasMore = false,
  loadingMore = false,
  loading: loadingProp,
  isBusiness,
  activeToolsetTypes = [],
  isServiceAccount = false,
  agentKey,
  onManageAgentToolsetCredentials,
}) => {
  const theme = useTheme();
  const { isAdmin } = useAdmin();

  // Local search input state — actual filtering is server-side
  const [searchInput, setSearchInput] = useState('');
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' | 'warning' | 'info' }>({
    open: false,
    message: '',
    severity: 'warning',
  });
  const [configDialogToolset, setConfigDialogToolset] = useState<ToolsetWithStatus | null>(null);

  // Debounce search input so we don't fire a request on every keystroke
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleSearchChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = e.target.value;
      setSearchInput(value);
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
      debounceTimer.current = setTimeout(() => {
        onSearch?.(value);
      }, 400);
    },
    [onSearch]
  );

  // Sentinel ref for IntersectionObserver (infinite scroll)
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const onLoadMoreRef = useRef(onLoadMore);
  onLoadMoreRef.current = onLoadMore;

  useEffect(() => {
    const sentinel = sentinelRef.current;
    let observer: IntersectionObserver | null = null;

    if (sentinel) {
      observer = new IntersectionObserver(
        (entries) => {
          if (entries[0].isIntersecting && !loadingMore && hasMore) {
            onLoadMoreRef.current?.();
          }
        },
        { threshold: 0.1 }
      );
      observer.observe(sentinel);
    }

    return () => {
      observer?.disconnect();
    };
  }, [loadingMore, hasMore]);

  const buildUIState = (toolset: ToolsetWithStatus) => {
    const isFromRegistry = toolset.isFromRegistry === true || !toolset.instanceId;

    // Service-account agent with a real (non-registry) instance → key icon to
    // open the agent credential dialog.  Show it green when already authenticated,
    // orange when creds are missing.
    if (isServiceAccount && !isFromRegistry) {
      const agentAuthenticated = (toolset as any).agentIsAuthenticated ?? toolset.isAuthenticated;
      return {
        isFromRegistry,
        configureTooltip: agentAuthenticated
          ? 'Manage agent credentials for this toolset'
          : 'Set agent credentials for this toolset',
        configureIcon: UI_ICONS.settings,
        configureIconColor: agentAuthenticated
          ? theme.palette.success.main
          : theme.palette.warning.main,
        // Always show the icon so the user can manage / refresh creds
        forceShowConfigureIcon: true,
      };
    }

    // Registry-only entry (no configured instance yet)
    if (isFromRegistry) {
      return {
        isFromRegistry,
        configureTooltip: (
          <>
            <span>Not configured (registry).</span>
            <br />
            <span>Admins can create an instance.</span>
          </>
        ),
        configureIcon: UI_ICONS.alertCircle,
        configureIconColor: theme.palette.error.main,
        forceShowConfigureIcon: true,
      };
    }

    // Regular user toolset (non-service-account)
    const configureTooltip =
      toolset.isConfigured && !toolset.isAuthenticated
        ? 'Authenticate this toolset'
        : 'Configure toolset';
    return {
      isFromRegistry,
      configureTooltip,
      configureIcon: UI_ICONS.settings,
      configureIconColor: theme.palette.warning.main,
      forceShowConfigureIcon: false,
    };
  };

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
          severity: 'success',
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

  // Group toolsets by toolset type (similar to connector grouping)
  const toolsetsByType = toolsets.reduce((acc, toolset) => {
    const toolsetType = (toolset as any).toolsetType || toolset.name || 'unknown';
    if (!acc[toolsetType]) {
      acc[toolsetType] = [];
    }
    acc[toolsetType].push(toolset);
    return acc;
  }, {} as Record<string, typeof toolsets>);

  // Handle configure click based on auth type
  const handleConfigureClick = async (toolset: ToolsetWithStatus) => {
    const authType = (toolset as any).authType || '';
    const instanceId = (toolset as any).instanceId || '';
    const isFromRegistry = (toolset as any).isFromRegistry === true || !instanceId;

    // For service-account agents, delegate configured instances to the
    // agent credentials dialog. Registry-only entries should follow the
    // normal registry redirect flow below.
    if (isServiceAccount && onManageAgentToolsetCredentials && !isFromRegistry) {
      onManageAgentToolsetCredentials(toolset);
      return;
    }

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
        severity: 'warning',
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
            severity: 'error',
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
            severity: 'error',
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
          severity: 'error',
        });
      }
    } else {
      // For other auth types (API Token, etc.): Open config dialog
      setConfigDialogToolset(toolset);
    }
  };

  const handleConfigDialogSuccess = async () => {
    setConfigDialogToolset(null);
    await refreshToolsets();
    setSnackbar({
      open: true,
      message: 'Authentication successful! Toolset is now ready to use.',
      severity: 'success',
    });
  };

  return (
    <Box sx={{ pl: 0 }}>
      {/* ── Server-side search input ── */}
      <Box sx={{ px: 1.5, pb: 1 }}>
        <TextField
          size="small"
          fullWidth
          placeholder="Search toolsets…"
          value={searchInput}
          onChange={handleSearchChange}
          InputProps={{
              startAdornment: (
              <InputAdornment position="start">
                <Icon icon="eva:search-fill" width={16} height={16} style={{ color: 'inherit', opacity: 0.6 }} />
              </InputAdornment>
            ),
          }}
          sx={{
            '& .MuiOutlinedInput-root': {
              fontSize: '0.75rem',
              borderRadius: 1.5,
            },
          }}
        />
      </Box>

      {/* ── Empty / loading states ── */}
      {!loading && toolsets.length === 0 && (
        <Box sx={{ pl: 4, py: 2 }}>
          <Typography
            variant="caption"
            sx={{
              color: alpha(theme.palette.text.secondary, 0.6),
              fontSize: '0.75rem',
              fontStyle: 'italic',
            }}
          >
            {searchInput ? `No toolsets match "${searchInput}"` : 'No toolsets available.'}
          </Typography>
        </Box>
      )}

      {/* ── Toolsets grouped by toolset type ── */}
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
          const { isFromRegistry, configureTooltip, configureIcon, configureIconColor, forceShowConfigureIcon } = buildUIState(toolset);
          
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
                severity: 'warning',
              });
              return;
            }
            const reason = !toolset.isConfigured ? 'not configured' : 'not authenticated';
            setSnackbar({
              open: true,
              message: `${toolset.displayName} is ${reason}. Please configure it before using.`,
              severity: 'warning',
            });
          };

          const handleDuplicateTypeDragAttempt = () => {
            setSnackbar({
              open: true,
              message: `Only one ${formatToolsetTypeLabel((toolset as any).toolsetType || toolset.name)} instance can be added to the flow at a time.`,
              severity: 'warning',
            });
          };

          // showConfigureIcon logic:
          //  • forceShowConfigureIcon = true for service-account non-registry (key icon)
          //    and for registry entries (alert-circle icon).
          //  • Regular (non-service-account) entries show settings when not yet configured/authed.
          const showCfgIcon = forceShowConfigureIcon || (!isServiceAccount && needsConfiguration);
          // Clickable whenever the icon is shown
          const cfgIconClickable = showCfgIcon;

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
              showConfigureIcon={showCfgIcon}
              showAuthenticatedIndicator={!needsConfiguration && toolset.isAuthenticated && !isServiceAccount}
              onConfigureClick={cfgIconClickable ? () => handleConfigureClick(toolset) : undefined}
              configureTooltip={configureTooltip}
              configureIcon={configureIcon}
              configureIconColor={configureIconColor}
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
                  const { isFromRegistry, configureTooltip, configureIcon, configureIconColor, forceShowConfigureIcon } = buildUIState(toolset);

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
                        severity: 'warning',
                      });
                      return;
                    }
                    const reason = !toolset.isConfigured ? 'not configured' : 'not authenticated';
                    setSnackbar({
                      open: true,
                      message: `${instanceName} is ${reason}. Please configure it before using.`,
                      severity: 'warning',
                    });
                  };

                  const handleDuplicateTypeDragAttempt = () => {
                    setSnackbar({
                      open: true,
                      message: `Only one ${formatToolsetTypeLabel((toolset as any).toolsetType || toolset.name)} instance can be added to the flow at a time.`,
                      severity: 'warning',
                    });
                  };

                  const showInstanceCfgIcon = forceShowConfigureIcon || (!isServiceAccount && needsConfiguration);
                  const instanceCfgClickable = showInstanceCfgIcon;

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
                      showConfigureIcon={showInstanceCfgIcon}
                      showAuthenticatedIndicator={!needsConfiguration && toolset.isAuthenticated && !isServiceAccount}
                      onConfigureClick={instanceCfgClickable ? () => handleConfigureClick(toolset) : undefined}
                      configureTooltip={configureTooltip}
                      configureIcon={configureIcon}
                      configureIconColor={configureIconColor}
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

      {/* ── Infinite scroll sentinel ── */}
      <Box
        ref={sentinelRef}
        sx={{ height: 4, display: 'flex', justifyContent: 'center', alignItems: 'center', mt: 0.5 }}
      >
        {loadingMore && (
          <CircularProgress size={18} thickness={4} sx={{ color: 'text.secondary', opacity: 0.6 }} />
        )}
      </Box>

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
            severity={snackbar.severity}
            sx={{ width: '100%' }}
          >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Portal>

      {/* Toolset Configuration Dialog */}
      {configDialogToolset && (
        <ToolsetConfigDialog
          toolset={configDialogToolset}
          toolsetId={configDialogToolset.instanceId}
          isAdmin={isAdmin}
          onClose={() => setConfigDialogToolset(null)}
          onSuccess={handleConfigDialogSuccess}
          onShowToast={(message) => {
            setSnackbar({ open: true, message, severity: 'success' });
          }}
        />
      )}
    </Box>
  );
};
