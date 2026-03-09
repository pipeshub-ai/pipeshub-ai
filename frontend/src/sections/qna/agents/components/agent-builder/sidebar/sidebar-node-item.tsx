// SidebarNodeItem Component
// Draggable node item for the flow builder sidebar

import React from 'react';
import { Box, ListItem, Typography, useTheme, alpha } from '@mui/material';
import { Icon } from '@iconify/react';
import { normalizeDisplayName } from '../../../utils/agent';
import { NodeTemplate, SidebarNodeItemProps } from './sidebar.types';

export const SidebarNodeItem: React.FC<SidebarNodeItemProps> = ({
  template,
  isSubItem = false,
  sectionType,
  connectorStatus,
  connectorInstance,
  connectorIconPath,
  itemIcon,
  isDynamicIcon = false,
  isDraggable = true,
}) => {
  const theme = useTheme();

  // Use neutral colors for minimal design
  const hoverColor = theme.palette.text.secondary;

  const handleDragStart = (event: React.DragEvent) => {
    event.dataTransfer.setData('application/reactflow', template.type);
    
    // For connector instances
    if (sectionType === 'connectors' && template.defaultConfig?.id) {
      event.dataTransfer.setData('connectorId', template.defaultConfig.id);
      event.dataTransfer.setData('connectorType', template.defaultConfig.type || '');
      event.dataTransfer.setData('scope', template.defaultConfig.scope || 'personal');
    }
    
    // For tools
    if (sectionType === 'tools') {
      // Check if this is a toolset tool (has toolsetName in defaultConfig)
      if (template.defaultConfig?.toolsetName) {
        // This is a tool from a toolset
        const toolsetName = template.defaultConfig.toolsetName || '';
        const toolName = template.defaultConfig.toolName || template.label || '';
        // Construct fullName if it's missing
        const fullName = template.defaultConfig.fullName || template.type || (toolsetName && toolName ? `${toolsetName}.${toolName}` : '');
        
        event.dataTransfer.setData('type', 'tool');
        event.dataTransfer.setData('instanceId', template.defaultConfig.instanceId || '');
        event.dataTransfer.setData('instanceName', template.defaultConfig.instanceName || '');
        event.dataTransfer.setData('toolsetType', template.defaultConfig.toolsetType || toolsetName);
        event.dataTransfer.setData('toolsetName', toolsetName);
        event.dataTransfer.setData('displayName', template.defaultConfig.displayName || '');
        event.dataTransfer.setData('toolName', toolName);
        event.dataTransfer.setData('fullName', fullName);
        event.dataTransfer.setData('description', template.defaultConfig.description || template.description || '');
        event.dataTransfer.setData('iconPath', template.defaultConfig.iconPath || '');
        event.dataTransfer.setData('isConfigured', String(template.defaultConfig.isConfigured || connectorStatus?.isConfigured || false));
        event.dataTransfer.setData('isAuthenticated', String(template.defaultConfig.isAuthenticated || connectorStatus?.isAgentActive || false));
        // Include all tools from toolset so toolset node can show them in add menu
        if (template.defaultConfig.allTools) {
          event.dataTransfer.setData('allTools', JSON.stringify(template.defaultConfig.allTools));
          event.dataTransfer.setData('toolCount', String(template.defaultConfig.allTools.length));
        }
      } else {
        // Regular connector tool
        event.dataTransfer.setData('toolAppName', template.defaultConfig?.appName || '');
        if (connectorStatus) {
          event.dataTransfer.setData('isConfigured', String(connectorStatus.isConfigured));
          event.dataTransfer.setData('isAgentActive', String(connectorStatus.isAgentActive));
        }
        if (connectorInstance) {
          event.dataTransfer.setData('connectorId', connectorInstance._key || (connectorInstance as any).id || '');
          event.dataTransfer.setData('connectorType', connectorInstance.type || '');
          event.dataTransfer.setData('connectorName', connectorInstance.name || '');
          event.dataTransfer.setData('scope', connectorInstance.scope || 'personal');
        }
        if (connectorIconPath) {
          event.dataTransfer.setData('connectorIconPath', connectorIconPath);
        }
      }
    }
  };

  const isDark = theme.palette.mode === 'dark';

  return (
    <ListItem
      button
      draggable={isDraggable}
      onDragStart={isDraggable ? handleDragStart : undefined}
      sx={{
        py: 0.75,
        px: 2,
        pl: isSubItem ? 5 : 3.5,
        cursor: isDraggable ? 'grab' : 'default',
        borderRadius: 1,
        mx: isSubItem ? 1.5 : 1,
        my: 0.25,
        border: 'none',
        backgroundColor: 'transparent',
        transition: 'all 0.2s ease',
        '&:hover': {
          backgroundColor: theme.palette.action.hover,
        },
        '&:active': {
          cursor: isDraggable ? 'grabbing' : 'default',
          backgroundColor: theme.palette.action.selected,
        },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.25, width: '100%' }}>
        {/* Minimal Icon */}
        {isDynamicIcon ? (
          <img
            src={typeof itemIcon === 'string' ? itemIcon : '/assets/icons/connectors/default.svg'}
            alt={template.label}
            width={isSubItem ? 14 : 16}
            height={isSubItem ? 14 : 16}
            style={{
              objectFit: 'contain',
              opacity: 0.7,
            }}
            onError={(e) => {
              e.currentTarget.src = '/assets/icons/connectors/default.svg';
            }}
          />
        ) : (
          <Icon
            icon={itemIcon || template.icon}
            width={isSubItem ? 14 : 16}
            height={isSubItem ? 14 : 16}
            style={{ color: theme.palette.text.secondary, opacity: 0.7 }}
          />
        )}
        
        {/* Label - Minimal Typography */}
        <Typography
          variant="body2"
          sx={{
            fontSize: isSubItem ? '0.8125rem' : '0.875rem',
            color: theme.palette.text.primary,
            fontWeight: 400,
            flex: 1,
            lineHeight: 1.5,
          }}
        >
          {normalizeDisplayName(template.label)}
        </Typography>
      </Box>
    </ListItem>
  );
};

