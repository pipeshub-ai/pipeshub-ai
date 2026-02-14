/**
 * SidebarToolsetsSection Component
 * Displays toolsets from in-memory registry with their tools for drag-and-drop
 * Shows configuration status and allows navigation to toolset settings for unconfigured ones
 * Similar pattern to connectors sidebar
 */

import React, { useState } from 'react';
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

import { SidebarCategory } from './sidebar-category';
import { SidebarNodeItem } from './sidebar-node-item';
import { getToolIcon } from './sidebar.icons';

interface SidebarToolsetsSectionProps {
  expandedApps: Record<string, boolean>;
  onAppToggle: (key: string) => void;
  toolsets: any[]; // Pre-loaded toolsets with status (isConfigured, isAuthenticated)
  loading: boolean; // Loading state from parent
  isBusiness?: boolean;
}

interface ToolsetWithStatus extends RegistryToolset {
  isConfigured: boolean;
  isAuthenticated: boolean;
}

export const SidebarToolsetsSection: React.FC<SidebarToolsetsSectionProps> = ({
  expandedApps,
  onAppToggle,
  toolsets: toolsetsProp,
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

  // Group filtered toolsets by category
  const toolsetsByCategory = filteredToolsets.reduce((acc, toolset) => {
    const category = toolset.category || 'app';
    if (!acc[category]) {
      acc[category] = [];
    }
    acc[category].push(toolset);
    return acc;
  }, {} as Record<string, typeof filteredToolsets>);

  // Category display names
  const categoryNames: Record<string, string> = {
    app: 'Applications',
    database: 'Databases',
    utility: 'Utilities',
    communication: 'Communication',
    productivity: 'Productivity',
    file: 'File Operations',
    web_search: 'Web Search',
    research: 'Research',
  };

  return (
    <Box sx={{ pl: 0 }}>
      {/* Toolsets Grouped by Category */}
      {Object.entries(toolsetsByCategory).map(([category, categoryToolsets]) => {
        const categoryKey = `category-${category}`;
        const isCategoryExpanded = expandedApps[categoryKey] ?? true; // Default expanded

        return (
          <Box key={category} sx={{ mb: 1 }}>
            {/* Category Header */}
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                px: 2,
                py: 1,
                pl: 4,
                cursor: 'pointer',
                '&:hover': {
                  backgroundColor: alpha(theme.palette.primary.main, 0.04),
                },
              }}
              onClick={() => onAppToggle(categoryKey)}
            >
              <Icon
                icon={isCategoryExpanded ? 'eva:arrow-down-fill' : 'eva:arrow-right-fill'}
                width={16}
                style={{ marginRight: 8, color: theme.palette.text.secondary }}
              />
              <Typography
                variant="subtitle2"
                sx={{
                  fontWeight: 600,
                  color: theme.palette.text.primary,
                  textTransform: 'uppercase',
                  fontSize: '0.75rem',
                  letterSpacing: '0.5px',
                }}
              >
                {categoryNames[category] || category}
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  ml: 1,
                  color: theme.palette.text.secondary,
                  fontSize: '0.7rem',
                }}
              >
                ({categoryToolsets.length})
              </Typography>
            </Box>

            {/* Toolsets in this category */}
            {isCategoryExpanded && categoryToolsets.map((toolset) => {
              const toolsetKey = `toolset-${(toolset as any).normalized_name || toolset.name.toLowerCase()}`;
              const isExpanded = expandedApps[toolsetKey];

              // Check if toolset needs configuration
              const needsConfiguration = !toolset.isConfigured || !toolset.isAuthenticated;

              // Create drag data for entire toolset
              // Format matches what the drop handler expects for toolset groups
              const toolsetDragData = {
                type: 'toolset',
                toolsetName: toolset.name,
                displayName: toolset.displayName,
                selectedTools: JSON.stringify(toolset.tools.map((t) => t.name)),
                allTools: JSON.stringify(
                  toolset.tools.map((t) => ({
                    toolName: t.name,
                    fullName: t.fullName || `${toolset.name}.${t.name}`,
                    toolsetName: toolset.name,
                    description: t.description,
                    appName: toolset.name,
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
                  message: `${toolset.displayName} is ${reason}. Please configure it in settings before using.`,
                });
              };

              // If toolset needs configuration, show configure icon
              if (needsConfiguration) {
                return (
                  <SidebarCategory
                    key={(toolset as any).normalized_name || toolset.name.toLowerCase()}
                    groupLabel={toolset.displayName}
                    groupIcon={toolset.iconPath || '/assets/icons/toolsets/default.svg'}
                    itemCount={toolset.tools.length}
                    isExpanded={isExpanded}
                    onToggle={() => onAppToggle(toolsetKey)}
                    borderColor={theme.palette.divider}
                    showConfigureIcon
                    onConfigureClick={() => {
                      if (isBusiness) {
                        window.location.href = '/account/company-settings/settings/toolsets';
                      } else {
                        window.location.href = '/account/individual/settings/toolsets';
                      }
                    }}
                    onDragAttempt={handleUnconfiguredDragAttempt}
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
                          // Create drag data for individual tool
                          const toolDragData = {
                            type: 'tool',
                            toolName: tool.name,
                            fullName: tool.fullName,
                            toolsetName: toolset.name,
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
                                isConfigured: false, 
                                isAgentActive: false 
                              }}
                              connectorIconPath={toolset.iconPath}
                              itemIcon={getToolIcon(tool.name, toolset.name)}
                            />
                          );
                        })}
                      </List>
                    </Box>
                  </SidebarCategory>
                );
              }

              // Toolset is configured - show as draggable
              // Use a more specific drag type that matches the pattern used by connectors
              const toolsetDragType = `toolset-${toolset.name.toLowerCase()}`;
              
              return (
                <SidebarCategory
                  key={(toolset as any).normalized_name || toolset.name.toLowerCase()}
                  groupLabel={toolset.displayName}
                  groupIcon={toolset.iconPath || '/assets/icons/toolsets/default.svg'}
                  itemCount={toolset.tools.length}
                  isExpanded={isExpanded}
                  onToggle={() => onAppToggle(toolsetKey)}
                  dragType={toolsetDragType}
                  dragData={toolsetDragData}
                  borderColor={theme.palette.divider}
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
                          // Create drag data for individual tool
                          // Ensure fullName is always set
                          const toolFullName = tool.fullName || `${toolset.name}.${tool.name}`;
                          
                          // Include all tools from toolset so toolset node can show them in add menu
                          const toolDragData = {
                            type: 'tool',
                            toolName: tool.name,
                            fullName: toolFullName,
                            toolsetName: toolset.name,
                            // Use toolset displayName only, not "toolset - tool" format
                            displayName: toolset.displayName,
                            description: tool.description,
                            iconPath: toolset.iconPath,
                            // Include all tools from toolset for the toolset node
                            allTools: toolset.tools.map((t: RegistryTool) => ({
                              toolName: t.name,
                              fullName: t.fullName || `${toolset.name}.${t.name}`,
                              toolsetName: toolset.name,
                              description: t.description,
                              appName: toolset.name,
                            })),
                            isConfigured: toolset.isConfigured,
                            isAuthenticated: toolset.isAuthenticated,
                          };

                          return (
                            <SidebarNodeItem
                              key={toolFullName}
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
                            />
                          );
                        })}
                      </List>
                  </Box>
                </SidebarCategory>
              );
            })}
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
