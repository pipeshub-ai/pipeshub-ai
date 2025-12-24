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
}) => {
  const theme = useTheme();

  // Get appropriate hover color based on section
  let hoverColor = theme.palette.primary.main;
  if (sectionType === 'apps') {
    hoverColor = theme.palette.info.main;
  } else if (sectionType === 'kbs') {
    hoverColor = theme.palette.warning.main;
  } else if (sectionType === 'connectors') {
    hoverColor = theme.palette.info.main;
  }

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
  };

  return (
    <ListItem
      button
      draggable
      onDragStart={handleDragStart}
      sx={{
        py: 0.75,
        px: 2,
        pl: isSubItem ? 5.5 : 4,
        cursor: 'grab',
        borderRadius: 1,
        mx: isSubItem ? 1.5 : 1,
        my: 0.25,
        border: `1px solid ${alpha(theme.palette.divider, 0.05)}`,
        backgroundColor: 'transparent',
        '&:hover': {
          backgroundColor: alpha(theme.palette.action.hover, 0.04),
          borderColor: alpha(theme.palette.divider, 0.1),
        },
        '&:active': {
          cursor: 'grabbing',
        },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, width: '100%' }}>
        {/* Icon */}
        {isDynamicIcon ? (
          <img
            src={typeof itemIcon === 'string' ? itemIcon : '/assets/icons/connectors/default.svg'}
            alt={template.label}
            width={isSubItem ? 16 : 18}
            height={isSubItem ? 16 : 18}
            style={{
              objectFit: 'contain',
            }}
            onError={(e) => {
              e.currentTarget.src = '/assets/icons/connectors/default.svg';
            }}
          />
        ) : (
          <Icon
            icon={itemIcon || template.icon}
            width={isSubItem ? 16 : 18}
            height={isSubItem ? 16 : 18}
            style={{ color: alpha(theme.palette.text.secondary, 0.7) }}
          />
        )}
        
        {/* Label */}
        <Typography
          variant="body2"
          sx={{
            fontSize: isSubItem ? '0.85rem' : '0.9rem',
            color: theme.palette.text.primary,
            fontWeight: 400,
            flex: 1,
            lineHeight: 1.4,
          }}
        >
          {normalizeDisplayName(template.label)}
        </Typography>
      </Box>
    </ListItem>
  );
};

