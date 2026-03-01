// Flow Builder Sidebar Component
// Main sidebar orchestrating all node templates and categories

import React, { useState, useMemo } from 'react';
import {
  Box,
  Drawer,
  List,
  ListItem,
  Typography,
  CircularProgress,
  Collapse,
  useTheme,
  alpha,
} from '@mui/material';
import { Icon } from '@iconify/react';
import { Connector } from 'src/sections/accountdetails/connectors/types/types';
import {
  SidebarHeader,
  SidebarNodeItem,
  SidebarCategory,
  SidebarToolsSection,
  SidebarKnowledgeSection,
  UI_ICONS,
  CATEGORY_ICONS,
  getAppKnowledgeIcon,
  getToolIcon,
  NodeTemplate,
  filterTemplatesBySearch,
  groupToolsByAppName,
  groupConnectorInstances,
  groupToolsByConnectorType,
} from './sidebar/index';
import { SidebarToolsetsSection } from './sidebar/sidebar-toolsets-section';
import { SidebarSkeleton } from '../skeleton-loader';

interface FlowBuilderSidebarProps {
  sidebarOpen: boolean;
  nodeTemplates: NodeTemplate[];
  loading: boolean;
  sidebarWidth: number;
  activeAgentConnectors: Connector[];
  configuredConnectors: Connector[];
  connectorRegistry: any[];
  toolsets: any[]; // Pre-loaded toolsets with status
  refreshToolsets: () => Promise<void>; // Refresh toolsets after OAuth
  isBusiness: boolean;
  userId?: string; // For toolsets section
}

const FlowBuilderSidebar: React.FC<FlowBuilderSidebarProps> = ({
  sidebarOpen,
  nodeTemplates,
  loading,
  sidebarWidth,
  activeAgentConnectors,
  configuredConnectors,
  connectorRegistry,
  toolsets,
  refreshToolsets,
  isBusiness,
  userId = '',
}) => {
  const theme = useTheme();
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>({
    'Input / Output': true,
    'LLM Models': false,
    Knowledge: false,
    Tools: true,
  });
  const [expandedApps, setExpandedApps] = useState<Record<string, boolean>>({});

  // Memoize all connectors
  const allConnectors = useMemo(() => [...configuredConnectors], [configuredConnectors]);

  // Filter templates based on search query
  const filteredTemplates = useMemo(
    () => filterTemplatesBySearch(nodeTemplates, searchQuery),
    [nodeTemplates, searchQuery]
  );

  // Group tools by app name
  const toolsByAppName = useMemo(
    () => groupToolsByAppName(filteredTemplates),
    [filteredTemplates]
  );

  // Group connector instances by type (for Knowledge section)
  const groupedConnectorInstances = useMemo(
    () => groupConnectorInstances(allConnectors),
    [allConnectors]
  );

  // Group tools by connector type using configured connectors
  // Uses configured (not active/sync-enabled) connectors so users see all their connectors
  const toolsGroupedByConnectorType = useMemo(
    () => groupToolsByConnectorType(toolsByAppName, configuredConnectors, connectorRegistry),
    [toolsByAppName, configuredConnectors, connectorRegistry]
  );

  // Get memory-related nodes for Knowledge section
  const kbGroupNode = useMemo(
    () => filteredTemplates.find((t: NodeTemplate) => t.type === 'kb-group'),
    [filteredTemplates]
  );

  const appKnowledgeGroupNode = useMemo(
    () => filteredTemplates.find((t: NodeTemplate) => t.type === 'app-group'),
    [filteredTemplates]
  );

  const individualKBs = useMemo(
    () =>
      filteredTemplates.filter(
        (t: NodeTemplate) => t.category === 'knowledge' && t.type.startsWith('kb-') && t.type !== 'kb-group'
      ),
    [filteredTemplates]
  );

  // Calculate actual Knowledge count (connector instances + individual KBs, excluding group nodes)
  const knowledgeCount = useMemo(() => {
    const connectorInstancesCount = Object.entries(groupedConnectorInstances).reduce(
      (acc, [_, data]) => acc + data.instances.length,
      0
    );
    return connectorInstancesCount + individualKBs.length;
  }, [groupedConnectorInstances, individualKBs]);

  const handleCategoryToggle = (categoryName: string) => {
    setExpandedCategories((prev) => ({
      ...prev,
      [categoryName]: !prev[categoryName],
    }));
  };

  const handleAppToggle = (appName: string) => {
    setExpandedApps((prev) => ({
      ...prev,
      [appName]: !prev[appName],
    }));
  };

  // Render draggable item with icon logic
  const renderDraggableItem = (
    template: NodeTemplate,
    isSubItem = false,
    sectionType?: 'tools' | 'apps' | 'kbs' | 'connectors'
  ) => {
    let itemIcon = template.icon;
    let isDynamicIcon = false;

    // Determine icon based on section type
    if (sectionType === 'apps' && template.defaultConfig?.appName) {
      const appName = template.defaultConfig.appName;
      const appIcon = getAppKnowledgeIcon(appName, allConnectors);
      if (appIcon === 'dynamic-icon') {
        isDynamicIcon = true;
        const connector = allConnectors.find(
          (c) =>
            c.name.toUpperCase() === appName.toUpperCase() ||
            c.name === appName
        );
        itemIcon = connector?.iconPath || '/assets/icons/connectors/collections-gray.svg';
      } else {
        if (typeof appIcon === 'string' || appIcon.toString().includes('/assets/icons/connectors/')) {
          isDynamicIcon = true;
        }
        itemIcon = appIcon;
      }
    } else if (sectionType === 'tools' && template.defaultConfig?.appName) {
      itemIcon = getToolIcon(template.type, template.defaultConfig.appName);
    } else if (sectionType === 'connectors' && template.defaultConfig?.name) {
      itemIcon = template.defaultConfig.iconPath || '/assets/icons/connectors/collections-gray.svg';
      isDynamicIcon = true;
    }

    // Generic string-path icon support
    if (!isDynamicIcon && typeof itemIcon === 'string') {
      isDynamicIcon = true;
    }

    // Input/output nodes should not be draggable
    const isDraggable = template.category !== 'inputs' && template.category !== 'outputs';

    return (
      <SidebarNodeItem
        key={template.type}
        template={template}
        isSubItem={isSubItem}
        sectionType={sectionType}
        itemIcon={itemIcon}
        isDynamicIcon={isDynamicIcon}
        isDraggable={isDraggable}
      />
    );
  };

  // Category configuration
  const categoryConfig = [
    {
      name: 'Input / Output',
      icon: CATEGORY_ICONS.inputOutput,
      categories: ['inputs', 'outputs'],
    },
    {
      name: 'LLM Models',
      icon: CATEGORY_ICONS.model,
      categories: ['llm'],
    },
    {
      name: 'Knowledge',
      icon: CATEGORY_ICONS.data,
      categories: ['knowledge'],
    },
    {
      name: 'Tools',
      icon: CATEGORY_ICONS.processing,
      categories: ['tools', 'connectors'],
    },
  ];

  const isDark = theme.palette.mode === 'dark';

  return (
    <Drawer
      variant="persistent"
      anchor="left"
      open={sidebarOpen}
      sx={{
        width: sidebarOpen ? sidebarWidth : 0,
        flexShrink: 0,
        transition: theme.transitions.create(['width'], {
          easing: theme.transitions.easing.easeInOut,
          duration: theme.transitions.duration.standard,
        }),
        '& .MuiDrawer-paper': {
          width: sidebarWidth,
          boxSizing: 'border-box',
          border: 'none',
          borderRight: `1px solid ${theme.palette.divider}`,
          backgroundColor: theme.palette.background.paper,
          zIndex: theme.zIndex.drawer - 1,
          position: 'relative',
          height: '100%',
          overflowX: 'hidden',
          boxShadow: 'none',
          transition: theme.transitions.create(['width'], {
            easing: theme.transitions.easing.easeInOut,
            duration: theme.transitions.duration.standard,
          }),
        },
      }}
    >
      {/* Header with Search */}
      <SidebarHeader searchQuery={searchQuery} onSearchChange={setSearchQuery} />

      {/* Sidebar Content */}
      <Box
        sx={{
          overflow: 'auto',
          height: 'calc(100% - 100px)',
          minHeight: 0,
          overflowX: 'hidden',
          '&::-webkit-scrollbar': {
            width: '6px',
          },
          '&::-webkit-scrollbar-track': {
            background: 'transparent',
          },
          '&::-webkit-scrollbar-thumb': {
            backgroundColor: alpha(theme.palette.text.secondary, 0.2),
            borderRadius: '3px',
            '&:hover': {
              backgroundColor: alpha(theme.palette.text.secondary, 0.3),
            },
          },
        }}
      >
        {loading ? (
          <SidebarSkeleton />
        ) : (
          <Box>
            {/* Main Categories */}
            {categoryConfig.map((config) => {
              const categoryTemplates = filteredTemplates.filter((t: NodeTemplate) =>
                config.categories.includes(t.category)
              );

              const isExpanded = expandedCategories[config.name];
              const hasItems =
                config.name === 'Tools'
                  ? Object.keys(toolsGroupedByConnectorType).length > 0
                  : categoryTemplates.length > 0;

              return (
                <Box key={config.name}>
                  {/* Category Header */}
                  <ListItem
                    button
                    onClick={() => handleCategoryToggle(config.name)}
                    sx={{
                      py: 1,
                      px: 2,
                      cursor: 'pointer',
                      borderRadius: 1,
                      mx: 1,
                      my: 0.25,
                      transition: 'all 0.2s ease',
                      '&:hover': {
                        backgroundColor: theme.palette.action.hover,
                      },
                    }}
                  >
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, width: '100%', position: 'relative', zIndex: 1 }}>
                      <Icon
                        icon={isExpanded ? UI_ICONS.chevronDown : UI_ICONS.chevronRight}
                        width={18}
                        height={18}
                        style={{ 
                          color: theme.palette.text.secondary,
                          transition: 'transform 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
                          transform: isExpanded ? 'rotate(0deg)' : 'rotate(0deg)',
                        }}
                      />
                      <Icon
                        icon={config.icon}
                        width={16}
                        height={16}
                        style={{ 
                          color: theme.palette.text.secondary,
                        }}
                      />
                      <Typography
                        variant="body2"
                        sx={{
                          flex: 1,
                          fontSize: '0.875rem',
                          color: theme.palette.text.primary,
                          fontWeight: isExpanded ? 600 : 500,
                        }}
                      >
                        {config.name}
                      </Typography>
                      {hasItems && (
                        <Typography
                          variant="caption"
                          sx={{
                            fontSize: '0.75rem',
                            fontWeight: 400,
                            color: theme.palette.text.secondary,
                          }}
                        >
                          {config.name === 'Tools' 
                            ? Object.keys(toolsGroupedByConnectorType).length
                            : config.name === 'Knowledge'
                            ? knowledgeCount
                            : categoryTemplates.length}
                        </Typography>
                      )}
                    </Box>
                  </ListItem>

                  {/* Category Content with Animation */}
                  <Collapse 
                    in={isExpanded} 
                    timeout={{
                      enter: 400,
                      exit: 300,
                    }}
                    easing={{
                      enter: 'cubic-bezier(0.4, 0, 0.2, 1)',
                      exit: 'cubic-bezier(0.4, 0, 0.2, 1)',
                    }}
                    unmountOnExit
                  >
                    {config.name === 'Tools' ? (
                      <SidebarToolsetsSection
                        expandedApps={expandedApps}
                        onAppToggle={handleAppToggle}
                        toolsets={toolsets}
                        refreshToolsets={refreshToolsets}
                        loading={loading}
                        isBusiness={isBusiness}
                      />
                    ) : config.name === 'LLM Models' ? (
                      <List dense sx={{ py: 0 }}>
                        {categoryTemplates.map((template: NodeTemplate) => renderDraggableItem(template))}
                      </List>
                    ) : config.name === 'Knowledge' ? (
                      <SidebarKnowledgeSection
                        groupedConnectorInstances={groupedConnectorInstances}
                        kbGroupNode={appKnowledgeGroupNode}
                        individualKBs={individualKBs}
                        expandedApps={expandedApps}
                        onAppToggle={handleAppToggle}
                      />
                    ) : hasItems ? (
                      <List dense sx={{ py: 0 }}>
                        {categoryTemplates.map((template: NodeTemplate) => renderDraggableItem(template))}
                      </List>
                    ) : (
                      <Box sx={{ pl: 4, py: 1 }}>
                        <Typography
                          variant="caption"
                          sx={{
                            color: alpha(theme.palette.text.secondary, 0.6),
                            fontSize: '0.75rem',
                            fontStyle: 'italic',
                          }}
                        >
                          No components available
                        </Typography>
                      </Box>
                    )}
                  </Collapse>
                </Box>
              );
            })}
          </Box>
        )}
      </Box>
    </Drawer>
  );
};

export default FlowBuilderSidebar;
