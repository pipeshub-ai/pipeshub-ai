/**
 * SidebarMcpServersSection Component
 * Displays configured MCP servers with discovered tools for drag-and-drop.
 * Shows auth status indicators and supports search with infinite scroll.
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

import type { MCPServerInstance, MCPServerTool } from 'src/types/agent';

import { SidebarCategory } from './sidebar-category';
import { SidebarNodeItem } from './sidebar-node-item';
import { getToolIcon, UI_ICONS } from './sidebar.icons';

interface SidebarMcpServersSectionProps {
  expandedApps: Record<string, boolean>;
  onAppToggle: (key: string) => void;
  mcpServers: MCPServerInstance[];
  refreshMcpServers: () => Promise<void>;
  onSearch?: (query: string) => void;
  onLoadMore?: () => Promise<void>;
  hasMore?: boolean;
  loadingMore?: boolean;
  loading: boolean;
  activeMcpServerTypes?: string[];
  isServiceAccount?: boolean;
  agentKey?: string;
  onManageAgentMcpCredentials?: (server: MCPServerInstance) => void;
}

const MCP_SERVER_ICON = 'solar:server-bold-duotone';

export const SidebarMcpServersSection: React.FC<SidebarMcpServersSectionProps> = ({
  expandedApps,
  onAppToggle,
  mcpServers,
  refreshMcpServers,
  onSearch,
  onLoadMore,
  hasMore = false,
  loadingMore = false,
  loading,
  activeMcpServerTypes = [],
  isServiceAccount = false,
  agentKey,
  onManageAgentMcpCredentials,
}) => {
  const theme = useTheme();

  const [searchInput, setSearchInput] = useState('');
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error' | 'warning' | 'info';
  }>({
    open: false,
    message: '',
    severity: 'warning',
  });

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

  const snackbarContainer =
    typeof window !== 'undefined' ? document.body : undefined;

  const buildUIState = (server: MCPServerInstance) => {
    const isFromRegistry = server.isFromRegistry === true || !server.instanceId;

    if (isServiceAccount && !isFromRegistry) {
      const agentAuthenticated =
        server.agentIsAuthenticated ?? server.isAuthenticated;
      return {
        isFromRegistry,
        configureTooltip: agentAuthenticated
          ? 'Manage agent credentials for this MCP server'
          : 'Set agent credentials for this MCP server',
        configureIcon: UI_ICONS.settings,
        configureIconColor: agentAuthenticated
          ? theme.palette.success.main
          : theme.palette.warning.main,
        forceShowConfigureIcon: true,
      };
    }

    if (isFromRegistry) {
      return {
        isFromRegistry,
        configureTooltip: (
          <>
            <span>Not configured (catalog).</span>
            <br />
            <span>Admins can create an instance.</span>
          </>
        ),
        configureIcon: UI_ICONS.alertCircle,
        configureIconColor: theme.palette.error.main,
        forceShowConfigureIcon: true,
      };
    }

    const configureTooltip =
      server.isConfigured && !server.isAuthenticated
        ? 'Authenticate this MCP server'
        : 'Configure MCP server';
    return {
      isFromRegistry,
      configureTooltip,
      configureIcon: UI_ICONS.settings,
      configureIconColor: theme.palette.warning.main,
      forceShowConfigureIcon: false,
    };
  };

  const handleConfigureClick = (server: MCPServerInstance) => {
    const isFromRegistry = server.isFromRegistry === true || !server.instanceId;

    if (isServiceAccount && onManageAgentMcpCredentials && !isFromRegistry) {
      onManageAgentMcpCredentials(server);
      return;
    }

    if (isFromRegistry) {
      setSnackbar({
        open: true,
        message: `${server.displayName} is not configured yet. Please set it up in MCP server settings.`,
        severity: 'warning',
      });
      return;
    }

    setSnackbar({
      open: true,
      message: `Please configure ${server.displayName} in the MCP server settings page.`,
      severity: 'info',
    });
  };

  const normalizedActiveMcpTypes = activeMcpServerTypes.map((t) =>
    (t || '').trim().toLowerCase().replace(/[\s_-]+/g, '')
  );

  return (
    <Box sx={{ pl: 0 }}>
      {/* Search input */}
      <Box sx={{ px: 1.5, pb: 1 }}>
        <TextField
          size="small"
          fullWidth
          placeholder="Search MCP servers…"
          value={searchInput}
          onChange={handleSearchChange}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Icon
                  icon="eva:search-fill"
                  width={16}
                  height={16}
                  style={{ color: 'inherit', opacity: 0.6 }}
                />
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

      {/* Empty / loading states */}
      {!loading && mcpServers.length === 0 && (
        <Box sx={{ pl: 4, py: 2 }}>
          <Typography
            variant="caption"
            sx={{
              color: alpha(theme.palette.text.secondary, 0.6),
              fontSize: '0.75rem',
              fontStyle: 'italic',
            }}
          >
            {searchInput
              ? `No MCP servers match "${searchInput}"`
              : 'No MCP servers available.'}
          </Typography>
        </Box>
      )}

      {/* MCP Servers list */}
      {mcpServers.map((server) => {
        const serverKey = `mcp-server-${server.instanceId || server.serverType}`;
        const isExpanded = expandedApps[serverKey];
        const needsConfiguration =
          !server.isConfigured || !server.isAuthenticated;
        const tools = server.tools || [];
        const normalizedServerType = (server.serverType || '')
          .trim()
          .toLowerCase()
          .replace(/[\s_-]+/g, '');
        const hasTypeAlreadyInFlow =
          normalizedActiveMcpTypes.includes(normalizedServerType);

        const {
          isFromRegistry,
          configureTooltip,
          configureIcon,
          configureIconColor,
          forceShowConfigureIcon,
        } = buildUIState(server);

        const dragData = {
          type: 'mcp-server',
          name: server.serverType || server.instanceName,
          displayName: server.displayName,
          instanceId: server.instanceId || '',
          instanceName: server.instanceName || server.displayName,
          serverType: server.serverType || '',
          tools: JSON.stringify(
            tools.map((t) => ({
              name: t.name,
              namespacedName: t.namespacedName,
              description: t.description,
            }))
          ),
          iconPath: server.iconPath || '',
          isConfigured: String(server.isConfigured ?? false),
          isAuthenticated: String(server.isAuthenticated ?? false),
          toolCount: String(tools.length),
        };

        const handleUnconfiguredDragAttempt = () => {
          if (isFromRegistry) {
            setSnackbar({
              open: true,
              message: `${server.displayName} is not configured yet. Please set it up in MCP server settings.`,
              severity: 'warning',
            });
            return;
          }
          const reason = !server.isConfigured
            ? 'not configured'
            : 'not authenticated';
          setSnackbar({
            open: true,
            message: `${server.displayName} is ${reason}. Please configure it before using.`,
            severity: 'warning',
          });
        };

        const handleDuplicateTypeDragAttempt = () => {
          setSnackbar({
            open: true,
            message: `Only one ${server.displayName} instance can be added to the flow at a time.`,
            severity: 'warning',
          });
        };

        const showCfgIcon =
          forceShowConfigureIcon || (!isServiceAccount && needsConfiguration);
        const cfgIconClickable = showCfgIcon;

        return (
          <SidebarCategory
            key={server.instanceId || server.serverType}
            groupLabel={server.displayName}
            groupIcon={server.iconPath || MCP_SERVER_ICON}
            itemCount={tools.length}
            isExpanded={isExpanded}
            onToggle={() => onAppToggle(serverKey)}
            dragType={
              needsConfiguration || hasTypeAlreadyInFlow
                ? undefined
                : `mcp-server-${(server.serverType || server.instanceName).toLowerCase()}`
            }
            dragData={
              needsConfiguration || hasTypeAlreadyInFlow ? undefined : dragData
            }
            borderColor={theme.palette.divider}
            showConfigureIcon={showCfgIcon}
            showAuthenticatedIndicator={
              !needsConfiguration &&
              server.isAuthenticated === true &&
              !isServiceAccount
            }
            onConfigureClick={
              cfgIconClickable ? () => handleConfigureClick(server) : undefined
            }
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
                {tools.map((tool: MCPServerTool) => {
                  const toolDragData = {
                    type: 'mcp-tool',
                    instanceId: server.instanceId || '',
                    instanceName: server.instanceName || server.displayName,
                    serverType: server.serverType || '',
                    toolName: tool.name,
                    namespacedName: tool.namespacedName,
                    displayName: `${server.displayName} - ${tool.name}`,
                    description: tool.description || '',
                    iconPath: server.iconPath || '',
                    allTools: JSON.stringify(
                      tools.map((t) => ({
                        name: t.name,
                        namespacedName: t.namespacedName,
                        description: t.description,
                      }))
                    ),
                    isConfigured: server.isConfigured ?? false,
                    isAuthenticated: server.isAuthenticated ?? false,
                  };

                  return (
                    <SidebarNodeItem
                      key={tool.namespacedName}
                      template={{
                        type: tool.namespacedName,
                        label: tool.name,
                        category: 'tools',
                        description: tool.description || '',
                        icon: server.iconPath || MCP_SERVER_ICON,
                        inputs: [],
                        outputs: [],
                        defaultConfig: {
                          ...toolDragData,
                        },
                      }}
                      isSubItem
                      sectionType="tools"
                      connectorStatus={{
                        isConfigured: server.isConfigured ?? false,
                        isAgentActive: server.isAuthenticated ?? false,
                      }}
                      connectorIconPath={server.iconPath}
                      itemIcon={getToolIcon(tool.name, server.displayName)}
                      isDraggable={!needsConfiguration}
                    />
                  );
                })}
              </List>
            </Box>
          </SidebarCategory>
        );
      })}

      {/* Infinite scroll sentinel */}
      <Box
        ref={sentinelRef}
        sx={{
          height: 4,
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          mt: 0.5,
        }}
      >
        {loadingMore && (
          <CircularProgress
            size={18}
            thickness={4}
            sx={{ color: 'text.secondary', opacity: 0.6 }}
          />
        )}
      </Box>

      {/* Snackbar */}
      <Portal container={snackbarContainer}>
        <Snackbar
          open={snackbar.open}
          autoHideDuration={5000}
          onClose={(_event, reason) => {
            if (reason === 'clickaway') return;
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
    </Box>
  );
};
