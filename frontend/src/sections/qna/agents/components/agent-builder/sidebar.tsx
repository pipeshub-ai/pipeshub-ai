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

interface FlowBuilderSidebarProps {
  sidebarOpen: boolean;
  nodeTemplates: NodeTemplate[];
  loading: boolean;
  sidebarWidth: number;
  activeAgentConnectors: Connector[];
  activeConnectors: Connector[];
  connectorRegistry: any[];
  isBusiness: boolean;
}

const FlowBuilderSidebar: React.FC<FlowBuilderSidebarProps> = ({
  sidebarOpen,
  nodeTemplates,
  loading,
  sidebarWidth,
  activeAgentConnectors,
  activeConnectors,
  connectorRegistry,
  isBusiness,
}) => {
  const theme = useTheme();
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>({
    'Input / Output': true,
    Agents: false,
    'LLM Models': false,
    Knowledge: false,
    Tools: true,
    'Vector Stores': false,
  });
  const [expandedApps, setExpandedApps] = useState<Record<string, boolean>>({});

  // Memoize all connectors
  const allConnectors = useMemo(() => [...activeConnectors], [activeConnectors]);

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

  // Group tools by connector type with agent instance info
  const toolsGroupedByConnectorType = useMemo(
    () => groupToolsByConnectorType(toolsByAppName, activeAgentConnectors, connectorRegistry),
    [toolsByAppName, activeAgentConnectors, connectorRegistry]
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
        itemIcon = connector?.iconPath || '/assets/icons/connectors/default.svg';
      } else {
        if (typeof appIcon === 'string' || appIcon.toString().includes('/assets/icons/connectors/')) {
          isDynamicIcon = true;
        }
        itemIcon = appIcon;
      }
    } else if (sectionType === 'tools' && template.defaultConfig?.appName) {
      itemIcon = getToolIcon(template.type, template.defaultConfig.appName);
    } else if (sectionType === 'connectors' && template.defaultConfig?.name) {
      itemIcon = template.defaultConfig.iconPath || '/assets/icons/connectors/default.svg';
      isDynamicIcon = true;
    }

    // Generic string-path icon support
    if (!isDynamicIcon && typeof itemIcon === 'string') {
      isDynamicIcon = true;
    }

    return (
      <SidebarNodeItem
        key={template.type}
        template={template}
        isSubItem={isSubItem}
        sectionType={sectionType}
        itemIcon={itemIcon}
        isDynamicIcon={isDynamicIcon}
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
      name: 'Agents',
      icon: CATEGORY_ICONS.agent,
      categories: ['agent'],
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
    {
      name: 'Vector Stores',
      icon: CATEGORY_ICONS.vector,
      categories: ['vector'],
    },
  ];

  return (
    <Drawer
      variant="persistent"
      anchor="left"
      open={sidebarOpen}
      sx={{
        width: sidebarOpen ? sidebarWidth : 0,
        flexShrink: 0,
        transition: theme.transitions.create(['width'], {
          easing: theme.transitions.easing.sharp,
          duration: theme.transitions.duration.leavingScreen,
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
            easing: theme.transitions.easing.sharp,
            duration: theme.transitions.duration.leavingScreen,
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
          height: 'calc(100% - 140px)',
          minHeight: 0,
          overflowX: 'hidden',
          '&::-webkit-scrollbar': {
            width: '4px',
          },
          '&::-webkit-scrollbar-track': {
            background: 'transparent',
          },
          '&::-webkit-scrollbar-thumb': {
            backgroundColor: alpha(theme.palette.text.secondary, 0.2),
            borderRadius: '8px',
            '&:hover': {
              backgroundColor: alpha(theme.palette.text.secondary, 0.3),
            },
          },
        }}
      >
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
            <CircularProgress size={24} />
          </Box>
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
                      '&:hover': {
                        backgroundColor: alpha(theme.palette.text.secondary, 0.05),
                      },
                    }}
                  >
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, width: '100%' }}>
                      <Icon
                        icon={isExpanded ? UI_ICONS.chevronDown : UI_ICONS.chevronRight}
                        width={16}
                        height={16}
                        style={{ color: theme.palette.text.secondary }}
                      />
                      <Icon
                        icon={config.icon}
                        width={16}
                        height={16}
                        style={{ color: theme.palette.text.secondary }}
                      />
                      <Typography
                        variant="body2"
                        sx={{
                          flex: 1,
                          fontSize: '0.875rem',
                          color: theme.palette.text.primary,
                          fontWeight: 500,
                        }}
                      >
                        {config.name}
                      </Typography>
                    </Box>
                  </ListItem>

                  {/* Category Content */}
                  <Collapse in={isExpanded} timeout="auto" unmountOnExit>
                    {config.name === 'Tools' ? (
                      <SidebarToolsSection
                        toolsGroupedByConnectorType={toolsGroupedByConnectorType}
                        expandedApps={expandedApps}
                        onAppToggle={handleAppToggle}
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
