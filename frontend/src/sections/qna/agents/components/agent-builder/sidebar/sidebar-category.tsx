import React from 'react';
import {
  Box,
  ListItem,
  Typography,
  IconButton,
  Tooltip,
  Collapse,
  useTheme,
  alpha,
} from '@mui/material';
import { Icon } from '@iconify/react';
import { UI_ICONS } from './sidebar.icons';

interface SidebarCategoryProps {
  groupLabel: string;
  groupIcon: any;
  itemCount: number;
  isExpanded: boolean;
  onToggle: () => void;
  dragType?: string;
  borderColor?: string;
  showConfigureIcon?: boolean;
  showAuthenticatedIndicator?: boolean; // Show green checkmark for authenticated toolsets
  onConfigureClick?: () => void;
  onDragAttempt?: () => void;
  dragData?: Record<string, any>;
  children?: React.ReactNode;
}

export const SidebarCategory: React.FC<SidebarCategoryProps> = ({
  groupLabel,
  groupIcon,
  itemCount,
  isExpanded,
  onToggle,
  dragType,
  borderColor,
  showConfigureIcon = false,
  showAuthenticatedIndicator = false,
  onConfigureClick,
  onDragAttempt,
  dragData,
  children,
}) => {
  const theme = useTheme();
  const effectiveBorderColor = borderColor || theme.palette.primary.main;

  const handleDragStart = (event: React.DragEvent) => {
    if (!dragType) {
      // If not draggable and has onDragAttempt, show toast
      if (onDragAttempt) {
        event.preventDefault();
        onDragAttempt();
      }
      return;
    }
    event.dataTransfer.setData('application/reactflow', dragType);
    if (dragData) {
      Object.entries(dragData).forEach(([key, value]) => {
        // Stringify arrays and objects, convert primitives to strings
        const stringValue = Array.isArray(value) || (typeof value === 'object' && value !== null)
          ? JSON.stringify(value)
          : String(value);
        event.dataTransfer.setData(key, stringValue);
      });
    }
  };

  return (
    <>
      <ListItem
        button
        draggable={!!dragType || !!onDragAttempt}
        onDragStart={handleDragStart}
        onClick={onToggle}
        sx={{
          py: 1,
          px: 2,
          pl: 4,
          cursor: dragType ? 'grab' : 'pointer',
          borderRadius: 1.5,
          mx: 1,
          mb: 0.5,
          border: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
          backgroundColor: 'transparent',
          '&:hover': {
            backgroundColor: alpha(theme.palette.action.hover, 0.04),
            borderColor: alpha(theme.palette.divider, 0.15),
            transform: dragType ? 'translateX(2px)' : 'none',
          },
          '&:active': {
            cursor: dragType ? 'grabbing' : 'pointer',
            transform: dragType ? 'scale(0.98)' : 'none',
          },
          transition: 'all 0.2s ease',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, width: '100%' }}>
          {/* Expand/Collapse Icon */}
          <Icon
            icon={isExpanded ? UI_ICONS.chevronDown : UI_ICONS.chevronRight}
            width={14}
            height={14}
            style={{ color: theme.palette.text.secondary }}
          />
          
          {/* Group Icon */}
          {typeof groupIcon === 'string' && groupIcon.includes('/assets/icons/connectors/') ? (
            <img
              src={groupIcon}
              alt={groupLabel}
              width={18}
              height={18}
              style={{ objectFit: 'contain' }}
              onError={(e) => {
                e.currentTarget.src = '/assets/icons/connectors/default.svg';
              }}
            />
          ) : (
            <Icon
              icon={groupIcon}
              width={18}
              height={18}
              style={{ color: theme.palette.text.secondary }}
            />
          )}
          
          {/* Label */}
          <Tooltip title={groupLabel} placement="right">
          <Typography
            variant="body2"
            sx={{
              flex: 1,
              fontSize: '0.875rem',
              color: theme.palette.text.primary,
              fontWeight: 500,
            }}
          >
              {groupLabel.length > 15 ? `${groupLabel.substring(0, 15)}...` : groupLabel}
            </Typography>
          </Tooltip>
          
          {/* Count Badge */}
          <Typography
            variant="caption"
            sx={{
              fontSize: '0.7rem',
              color: alpha(theme.palette.text.secondary, 0.6),
              fontWeight: 500,
              backgroundColor: alpha(theme.palette.text.secondary, 0.1),
              px: 0.75,
              py: 0.25,
              borderRadius: 1,
            }}
          >
            {itemCount}
          </Typography>
          
          {/* Authenticated Indicator (Green Checkmark) */}
          {showAuthenticatedIndicator && (
            <Tooltip title="Authenticated" placement="right">
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  ml: 0.5,
                }}
              >
                <Icon 
                  icon="mdi:check-circle" 
                  width={16} 
                  height={16}
                  style={{ 
                    color: theme.palette.success.main,
                  }}
                />
              </Box>
            </Tooltip>
          )}
          
          {/* Configure Icon */}
          {showConfigureIcon && onConfigureClick && (
            <Tooltip title="Configure connector" placement="right">
              <IconButton
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  onConfigureClick();
                }}
                sx={{
                  ml: 0.5,
                  color: theme.palette.error.main,
                  '&:hover': {
                    backgroundColor: alpha(theme.palette.error.main, 0.1),
                  },
                }}
              >
                <Icon icon={UI_ICONS.settings} width={16} height={16} />
              </IconButton>
            </Tooltip>
          )}
        </Box>
      </ListItem>

      {/* Collapsible Content */}
      <Collapse in={isExpanded} timeout="auto" unmountOnExit>
        {children}
      </Collapse>
    </>
  );
};

