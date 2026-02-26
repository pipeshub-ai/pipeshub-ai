// Node Icon Component
// Handles icon display logic for different node types

import React from 'react';
import { Box, alpha } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { Icon } from '@iconify/react';
import { NodeData } from '../../../types/agent';
import {
  hasConnectorIcon,
  getConnectorIconPath,
  getDefaultIconForNodeType,
  isIndividualToolNode,
} from './node.utils';

interface NodeIconProps {
  data: NodeData;
  toolIcon?: any;
  size?: number;
}

/**
 * Dual Icon Display for Individual Tool Nodes
 * Shows both connector icon and tool-specific action icon
 */
const DualToolIcon: React.FC<{
  connectorIconPath: string;
  toolIcon: any;
  iconFromData: any;
}> = ({ connectorIconPath, toolIcon, iconFromData }) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 0.75,
        px: 1,
        py: 0.5,
        borderRadius: 1.5,
        backgroundColor: alpha(
          isDark ? theme.palette.grey[800] : theme.palette.grey[100],
          0.5
        ),
        border: `1px solid ${
          isDark ? theme.palette.grey[700] : theme.palette.grey[300]
        }`,
      }}
    >
      {/* Connector Icon (Smaller) */}
      <img
        src={connectorIconPath}
        alt="connector"
        width={14}
        height={14}
        style={{
          objectFit: 'contain',
        }}
        onError={(e) => {
          e.currentTarget.src = '/assets/icons/connectors/collections-gray.svg';
        }}
      />
      
      {/* Tool Action Icon (Regular) */}
      <Icon
        icon={iconFromData || toolIcon}
        width={18}
        height={18}
        style={{
          color: theme.palette.text.primary,
        }}
      />
    </Box>
  );
};

/**
 * Single Icon Display
 * Standard icon display for most node types
 */
const SingleIcon: React.FC<{
  data: NodeData;
  toolIcon?: any;
  size?: number;
}> = ({ data, toolIcon, size = 24 }) => {
  const theme = useTheme();
  const iconPath = getConnectorIconPath(data);
  const icon = data.icon || toolIcon;

  // If node has an image icon path, use it
  if (iconPath) {
    return (
      <img
        src={iconPath}
        alt={data.label || 'icon'}
        width={size}
        height={size}
        style={{
          objectFit: 'contain',
        }}
        onError={(e) => {
          e.currentTarget.src = getDefaultIconForNodeType(data.type);
        }}
      />
    );
  }

  // Otherwise, use Iconify icon
  if (icon) {
    return (
      <Icon
        icon={icon}
        width={size}
        height={size}
        style={{
          color: theme.palette.text.primary,
        }}
      />
    );
  }

  // Fallback: no icon
  return null;
};

/**
 * Main Node Icon Component
 * Intelligently displays icons based on node type and configuration
 */
export const NodeIcon: React.FC<NodeIconProps> = ({
  data,
  toolIcon,
  size = 24,
}) => {
  // Check if this is an individual tool with connector icon
  // If so, display dual icons
  if (hasConnectorIcon(data)) {
    const connectorIconPath = getConnectorIconPath(data);
    
    if (connectorIconPath) {
      return (
        <DualToolIcon
          connectorIconPath={connectorIconPath}
          toolIcon={toolIcon}
          iconFromData={data.icon}
        />
      );
    }
  }

  // For all other cases, display single icon
  return <SingleIcon data={data} toolIcon={toolIcon} size={size} />;
};

/**
 * Connector Icon Only (for tool groups)
 * Used when we only want to show the connector's icon
 */
export const ConnectorIcon: React.FC<{
  iconPath: string;
  size?: number;
  alt?: string;
}> = ({ iconPath, size = 24, alt = 'connector' }) => (
  <img
    src={iconPath}
    alt={alt}
    width={size}
    height={size}
    style={{
      objectFit: 'contain',
    }}
    onError={(e) => {
      e.currentTarget.src = '/assets/icons/connectors/collections-gray.svg';
    }}
  />
);

