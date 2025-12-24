// SidebarToolsSection Component
// Complex tools section rendering with connector grouping

import React from 'react';
import { Box, List, Typography, useTheme, alpha } from '@mui/material';
import { SidebarCategory } from './sidebar-category';
import { SidebarNodeItem } from './sidebar-node-item';
import { getToolIcon } from './sidebar.icons';
import { NodeTemplate, SidebarToolsSectionProps } from './sidebar.types';
import {
  connectorNeedsConfiguration,
  createToolGroupDragData,
} from './sidebar.utils';
import { normalizeDisplayName } from '../../../utils/agent';

export const SidebarToolsSection: React.FC<SidebarToolsSectionProps> = ({
  toolsGroupedByConnectorType,
  expandedApps,
  onAppToggle,
  isBusiness
}) => {
  const theme = useTheme();

  if (Object.keys(toolsGroupedByConnectorType).length === 0) {
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
          No tools available.
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ pl: 0 }}>
      {Object.entries(toolsGroupedByConnectorType).map(([displayName, data]) => {
        const { connectorIcon, tools, activeAgentInstances, isConfigured, isAgentActive } = data;
        const connectorTypeKey = `tool-type-${displayName}`;
        const isTypeExpanded = expandedApps[connectorTypeKey];
        const needsConfiguration = connectorNeedsConfiguration(
          activeAgentInstances,
          isConfigured,
          isAgentActive
        );
        const connectorStatusObj = { isConfigured, isAgentActive };

        // Needs configuration - show with configure icon
        if (needsConfiguration) {
          return (
            <SidebarCategory
              key={displayName}
              groupLabel={displayName}
              groupIcon={connectorIcon}
              itemCount={tools.length}
              isExpanded={isTypeExpanded}
              onToggle={() => onAppToggle(connectorTypeKey)}
              borderColor={theme.palette.divider}
              showConfigureIcon
              onConfigureClick={() => {
                if (isBusiness) {
                  window.location.href = '/account/company-settings/settings/connector/';
                } else {
                  window.location.href = '/account/individual/settings/connector';
                } 
              }}
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
                  {tools.map((tool) => (
                    <SidebarNodeItem
                      key={tool.type}
                      template={tool}
                      isSubItem
                      sectionType="tools"
                      connectorStatus={connectorStatusObj}
                      connectorIconPath={connectorIcon}
                      itemIcon={getToolIcon(tool.type, tool.defaultConfig?.appName || '')}
                    />
                  ))}
                </List>
              </Box>
            </SidebarCategory>
          );
        }

        // Single active configured instance - make whole group draggable
        if (activeAgentInstances.length === 1) {
          const instance = activeAgentInstances[0];
          const instanceKey = `tool-instance-${instance._key || instance.name}`;
          const isInstanceExpanded = expandedApps[instanceKey];
          const toolGroupDragType = `tool-group-${data.connectorType.toLowerCase()}`;
          const dragData = createToolGroupDragData(
            tools,
            instance,
            data.connectorType,
            connectorIcon,
            isConfigured,
            isAgentActive
          );

          return (
            <SidebarCategory
              key={displayName}
              groupLabel={displayName}
              groupIcon={connectorIcon}
              itemCount={tools.length}
              isExpanded={isInstanceExpanded}
              onToggle={() => onAppToggle(instanceKey)}
              dragType={toolGroupDragType}
              borderColor={theme.palette.divider}
              dragData={dragData}
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
                  {tools.map((tool) => (
                    <SidebarNodeItem
                      key={tool.type}
                      template={tool}
                      isSubItem
                      sectionType="tools"
                      connectorStatus={connectorStatusObj}
                      connectorInstance={instance}
                      connectorIconPath={connectorIcon}
                      itemIcon={getToolIcon(tool.type, tool.defaultConfig?.appName || '')}
                    />
                  ))}
                </List>
              </Box>
            </SidebarCategory>
          );
        }

        // Multiple active instances - each instance draggable with all tools
        return (
          <SidebarCategory
            key={displayName}
            groupLabel={displayName}
            groupIcon={connectorIcon}
            itemCount={activeAgentInstances.length}
            isExpanded={isTypeExpanded}
            onToggle={() => onAppToggle(connectorTypeKey)}
            borderColor={theme.palette.divider}
          >
            <Box sx={{ pl: 2 }}>
              {activeAgentInstances.map((instance: any) => {
                const instanceKey = `tool-instance-${instance._key || instance.id}`;
                const isInstanceExpanded = expandedApps[instanceKey];
                const toolGroupDragType = `tool-group-${data.connectorType.toLowerCase()}`;
                const dragData = createToolGroupDragData(
                  tools,
                  instance,
                  data.connectorType,
                  connectorIcon,
                  isConfigured,
                  isAgentActive
                );

                return (
                  <SidebarCategory
                    key={instance._key || instance.id}
                    groupLabel={normalizeDisplayName(instance.name)}
                    groupIcon={connectorIcon}
                    itemCount={tools.length}
                    isExpanded={isInstanceExpanded}
                    onToggle={() => onAppToggle(instanceKey)}
                    dragType={toolGroupDragType}
                    borderColor={theme.palette.divider}
                    dragData={dragData}
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
                        {tools.map((tool) => (
                          <SidebarNodeItem
                            key={tool.type}
                            template={tool}
                            isSubItem
                            sectionType="tools"
                            connectorStatus={connectorStatusObj}
                            connectorInstance={instance}
                            connectorIconPath={connectorIcon}
                            itemIcon={getToolIcon(tool.type, tool.defaultConfig?.appName || '')}
                          />
                        ))}
                      </List>
                    </Box>
                  </SidebarCategory>
                );
              })}
            </Box>
          </SidebarCategory>
        );
      })}
    </Box>
  );
};

