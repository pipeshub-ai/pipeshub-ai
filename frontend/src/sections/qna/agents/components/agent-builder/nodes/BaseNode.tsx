// Base Node Component
// Base component providing common structure for all node types

import React, { ReactNode } from 'react';
import { Card, CardContent, Typography, Box, Chip } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import { NodeData } from '../../../types/agent';
import { NodeHandles } from './NodeHandles';
import { NodeActions } from './NodeActions';
import { NodeIcon } from './NodeIcon';
import { getNodeDisplayLabel, isNodeConfigured } from './node.utils';

interface BaseNodeProps {
  id: string;
  data: NodeData;
  selected?: boolean;
  toolIcon?: any;
  onDelete?: (nodeId: string) => void;
  children?: ReactNode;
  customHeader?: ReactNode;
  customContent?: ReactNode;
  showDefaultIcon?: boolean;
  showDefaultLabel?: boolean;
  minWidth?: number;
  minHeight?: number;
}

/**
 * Base Node Component
 * Provides common structure and styling for all node types
 */
export const BaseNode: React.FC<BaseNodeProps> = ({
  id,
  data,
  selected = false,
  toolIcon,
  onDelete,
  children,
  customHeader,
  customContent,
  showDefaultIcon = true,
  showDefaultLabel = true,
  minWidth = 280,
  minHeight = 120,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const isConfigured = isNodeConfigured(data);
  const displayLabel = getNodeDisplayLabel(data);

  return (
    <Card
      sx={{
        minWidth,
        minHeight,
        position: 'relative',
        backgroundColor: isDark
          ? alpha(theme.palette.background.paper, 0.95)
          : theme.palette.background.paper,
        border: selected
          ? `2px solid ${theme.palette.primary.main}`
          : `1px solid ${
              isDark ? theme.palette.grey[800] : theme.palette.grey[300]
            }`,
        borderRadius: 2,
        boxShadow: selected
          ? `0 0 0 4px ${alpha(theme.palette.primary.main, 0.15)}`
          : isDark
          ? '0 2px 8px rgba(0, 0, 0, 0.5)'
          : '0 2px 8px rgba(0, 0, 0, 0.1)',
        transition: 'all 0.2s ease',
        cursor: 'pointer',
        '&:hover': {
          boxShadow: selected
            ? `0 0 0 4px ${alpha(theme.palette.primary.main, 0.25)}`
            : isDark
            ? '0 4px 12px rgba(0, 0, 0, 0.6)'
            : '0 4px 12px rgba(0, 0, 0, 0.15)',
        },
      }}
    >
      {/* Handles */}
      <NodeHandles data={data} />

      {/* Action Buttons */}
      <NodeActions data={data} nodeId={id} onDelete={onDelete} />

      <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
        {/* Custom Header or Default Header */}
        {customHeader || (
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1.5,
              mb: customContent ? 1.5 : 0,
            }}
          >
            {/* Icon */}
            {showDefaultIcon && <NodeIcon data={data} toolIcon={toolIcon} size={24} />}

            {/* Label */}
            {showDefaultLabel && (
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography
                  variant="subtitle2"
                  sx={{
                    fontWeight: 600,
                    color: theme.palette.text.primary,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {displayLabel}
                </Typography>
              </Box>
            )}

            {/* Configuration Status Badge */}
            {!isConfigured && (
              <Chip
                label="Not Configured"
                size="small"
                sx={{
                  height: 20,
                  fontSize: '0.7rem',
                  backgroundColor: alpha(theme.palette.warning.main, 0.15),
                  color: theme.palette.warning.main,
                  fontWeight: 600,
                }}
              />
            )}
          </Box>
        )}

        {/* Custom Content */}
        {customContent}

        {/* Children (additional content) */}
        {children}
      </CardContent>
    </Card>
  );
};

/**
 * Compact Node Variant
 * Smaller, minimal version for simple nodes
 */
export const CompactNode: React.FC<BaseNodeProps> = (props) => (
  <BaseNode
    {...props}
    minWidth={240}
    minHeight={100}
    showDefaultLabel
    showDefaultIcon
  />
);

/**
 * Large Node Variant
 * Larger version for complex nodes with more content
 */
export const LargeNode: React.FC<BaseNodeProps> = (props) => (
  <BaseNode
    {...props}
    minWidth={420}
    minHeight={200}
    showDefaultLabel
    showDefaultIcon
  />
);

