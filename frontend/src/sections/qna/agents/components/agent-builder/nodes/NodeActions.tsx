// Node Actions Component
// Handles action buttons for nodes (delete, configure, etc.)

import React from 'react';
import { IconButton, Tooltip } from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import { Icon } from '@iconify/react';
import trashIcon from '@iconify-icons/mdi/trash-can-outline';
import { canDeleteNode } from './node.utils';
import { NodeData } from '../../../types/agent';

interface NodeActionsProps {
  data: NodeData;
  nodeId: string;
  onDelete?: (nodeId: string) => void;
}

/**
 * Delete Button Component
 * Displays a delete button for deletable nodes
 */
export const NodeDeleteButton: React.FC<NodeActionsProps> = ({
  data,
  nodeId,
  onDelete,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';

  // Check if node can be deleted
  if (!canDeleteNode(data.type)) {
    return null;
  }

  // Check if delete handler is provided
  if (!onDelete) {
    return null;
  }

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    onDelete(nodeId);
  };

  return (
    <Tooltip title="Delete node" placement="top">
      <IconButton
        size="small"
        onClick={handleDelete}
        sx={{
          position: 'absolute',
          top: 8,
          right: 8,
          width: 28,
          height: 28,
          backgroundColor: alpha(
            theme.palette.error.main,
            isDark ? 0.15 : 0.08
          ),
          color: theme.palette.error.main,
          '&:hover': {
            backgroundColor: alpha(
              theme.palette.error.main,
              isDark ? 0.25 : 0.15
            ),
            transform: 'scale(1.05)',
          },
          transition: 'all 0.2s ease',
          zIndex: 10,
        }}
      >
        <Icon icon={trashIcon} width={16} height={16} />
      </IconButton>
    </Tooltip>
  );
};

/**
 * Node Actions Container
 * Container for all node action buttons
 */
export const NodeActions: React.FC<NodeActionsProps> = ({
  data,
  nodeId,
  onDelete,
}) => (
  <>
    <NodeDeleteButton data={data} nodeId={nodeId} onDelete={onDelete} />
    {/* Add more action buttons here as needed */}
  </>
);

