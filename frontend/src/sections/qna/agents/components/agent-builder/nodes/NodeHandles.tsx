// Node Handles Component
// Separated component for rendering input and output handles

import React from 'react';
import { Handle, Position } from '@xyflow/react';
import { alpha, useTheme } from '@mui/material/styles';
import { NodeData } from '../../../types/agent';
import {
  shouldShowInputHandles,
  shouldShowOutputHandles,
  calculateHandlePosition,
} from './node.utils';
import { HANDLE_CONFIG } from './node.constants';

interface NodeHandlesProps {
  data: NodeData;
}

/**
 * Input Handles Component
 * Renders all input handles for a node based on its configuration
 */
export const NodeInputHandles: React.FC<NodeHandlesProps> = ({ data }) => {
  const theme = useTheme();
  const nodeType = data.type;

  // Check if this node type should display input handles
  if (!shouldShowInputHandles(nodeType)) {
    return null;
  }

  // If no inputs defined, don't render anything
  if (!data.inputs || data.inputs.length === 0) {
    return null;
  }

  const handleCount = data.inputs.length;

  return (
    <>
      {data.inputs.map((input: string, index: number) => {
        const topPosition = calculateHandlePosition(
          index,
          handleCount,
          HANDLE_CONFIG.INPUT.POSITION_OFFSET,
          HANDLE_CONFIG.INPUT.POSITION_INCREMENT
        );

        return (
          <Handle
            key={`input-${input}-${index}`}
            type="target"
            position={Position.Left}
            id={input}
            style={{
              top: topPosition,
              left: HANDLE_CONFIG.INPUT.OFFSET_LEFT,
              width: HANDLE_CONFIG.INPUT.SIZE,
              height: HANDLE_CONFIG.INPUT.SIZE,
              backgroundColor: theme.palette.background.paper,
              border: `${HANDLE_CONFIG.INPUT.BORDER_WIDTH}px solid ${theme.palette.primary.main}`,
              borderRadius: '50%',
              cursor: 'crosshair',
              zIndex: HANDLE_CONFIG.INPUT.Z_INDEX,
              transition: 'all 0.2s ease',
              boxShadow: `0 0 0 2px ${alpha(theme.palette.primary.main, 0.1)}`,
              transformOrigin: 'center',
            }}
            onMouseEnter={(e: React.MouseEvent<HTMLDivElement>) => {
              e.currentTarget.style.transform = 'scale(1.2)';
              e.currentTarget.style.boxShadow = `0 0 0 4px ${alpha(
                theme.palette.primary.main,
                0.2
              )}`;
            }}
            onMouseLeave={(e: React.MouseEvent<HTMLDivElement>) => {
              e.currentTarget.style.transform = 'scale(1)';
              e.currentTarget.style.boxShadow = `0 0 0 2px ${alpha(
                theme.palette.primary.main,
                0.1
              )}`;
            }}
          />
        );
      })}
    </>
  );
};

/**
 * Output Handles Component
 * Renders all output handles for a node based on its configuration
 */
export const NodeOutputHandles: React.FC<NodeHandlesProps> = ({ data }) => {
  const theme = useTheme();
  const nodeType = data.type;

  // Check if this node type should display output handles
  if (!shouldShowOutputHandles(nodeType)) {
    return null;
  }

  // If no outputs defined, don't render anything
  if (!data.outputs || data.outputs.length === 0) {
    return null;
  }

  const handleCount = data.outputs.length;

  return (
    <>
      {data.outputs.map((output: string, index: number) => {
        const topPosition = calculateHandlePosition(
          index,
          handleCount,
          HANDLE_CONFIG.OUTPUT.POSITION_OFFSET,
          HANDLE_CONFIG.OUTPUT.POSITION_INCREMENT
        );

        return (
          <Handle
            key={`output-${output}-${index}`}
            type="source"
            position={Position.Right}
            id={output}
            style={{
              top: topPosition,
              right: HANDLE_CONFIG.OUTPUT.OFFSET_RIGHT,
              width: HANDLE_CONFIG.OUTPUT.SIZE,
              height: HANDLE_CONFIG.OUTPUT.SIZE,
              backgroundColor: theme.palette.background.paper,
              border: `${HANDLE_CONFIG.OUTPUT.BORDER_WIDTH}px solid ${theme.palette.success.main}`,
              borderRadius: '50%',
              cursor: 'crosshair',
              zIndex: HANDLE_CONFIG.OUTPUT.Z_INDEX,
              boxShadow: `0 0 0 2px ${alpha(theme.palette.success.main, 0.1)}`,
              transformOrigin: 'center',
            }}
          />
        );
      })}
    </>
  );
};

/**
 * Combined Handles Component
 * Convenience component that renders both input and output handles
 */
export const NodeHandles: React.FC<NodeHandlesProps> = ({ data }) => (
  <>
    <NodeInputHandles data={data} />
    <NodeOutputHandles data={data} />
  </>
);

