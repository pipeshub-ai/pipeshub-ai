// Node Handles Component
// Separated component for rendering input and output handles

import React from 'react';
import { Handle, Position } from '@xyflow/react';
import { alpha, useTheme } from '@mui/material/styles';
import { Box, Typography } from '@mui/material';
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
              backgroundColor: theme.palette.text.secondary,
              border: `1px solid ${theme.palette.background.paper}`,
              borderRadius: '50%',
              cursor: 'crosshair',
              zIndex: HANDLE_CONFIG.INPUT.Z_INDEX,
              transition: 'all 0.15s ease',
            }}
            onMouseEnter={(e: React.MouseEvent<HTMLDivElement>) => {
              e.currentTarget.style.backgroundColor = theme.palette.text.primary;
              e.currentTarget.style.transform = 'scale(1.2)';
            }}
            onMouseLeave={(e: React.MouseEvent<HTMLDivElement>) => {
              e.currentTarget.style.backgroundColor = theme.palette.text.secondary;
              e.currentTarget.style.transform = 'scale(1)';
            }}
          />
        );
      })}
    </>
  );
};

// Map output handle IDs to label + color for conditional-check nodes
const CONDITIONAL_HANDLE_META: Record<string, { label: string; color: string; hoverColor: string }> = {
  pass: { label: 'Pass', color: '#58b78a', hoverColor: '#469f77' },
  fail: { label: 'Fail', color: '#d98989', hoverColor: '#c87777' },
};

/**
 * Output Handles Component
 * Renders all output handles for a node based on its configuration.
 * For conditional-check nodes, pass/fail handles get distinct colors and labels.
 */
export const NodeOutputHandles: React.FC<NodeHandlesProps> = ({ data }) => {
  const theme = useTheme();
  const nodeType = data.type;
  const isConditional = nodeType === 'conditional-check';

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

        const meta = isConditional ? CONDITIONAL_HANDLE_META[output] : undefined;
        const baseColor = meta ? meta.color : theme.palette.text.secondary;
        const hoverColor = meta ? meta.hoverColor : theme.palette.text.primary;

        return (
          <React.Fragment key={`output-frag-${output}-${index}`}>
            {/* Inline label badge for conditional pass/fail handles */}
            {meta && (
              <Box
                sx={{
                  position: 'absolute',
                  top: topPosition,
                  right: -52,
                  transform: 'translateY(-50%)',
                  zIndex: (HANDLE_CONFIG.OUTPUT.Z_INDEX ?? 10) + 1,
                  pointerEvents: 'none',
                  userSelect: 'none',
                }}
              >
                <Typography
                  component="span"
                  sx={{
                    display: 'inline-block',
                    fontSize: '0.58rem',
                    fontWeight: 700,
                    color: meta.color,
                    backgroundColor: alpha(meta.color, 0.12),
                    border: `1px solid ${alpha(meta.color, 0.35)}`,
                    borderRadius: '4px',
                    px: 0.6,
                    py: 0.1,
                    letterSpacing: '0.04em',
                    textTransform: 'uppercase',
                    lineHeight: 1.6,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {meta.label}
                </Typography>
              </Box>
            )}

            <Handle
              type="source"
              position={Position.Right}
              id={output}
              style={{
                top: topPosition,
                right: HANDLE_CONFIG.OUTPUT.OFFSET_RIGHT,
                width: HANDLE_CONFIG.OUTPUT.SIZE,
                height: HANDLE_CONFIG.OUTPUT.SIZE,
                backgroundColor: baseColor,
                border: meta
                  ? `2px solid ${alpha(baseColor, 0.45)}`
                  : `1px solid ${theme.palette.background.paper}`,
                borderRadius: '50%',
                cursor: 'crosshair',
                zIndex: HANDLE_CONFIG.OUTPUT.Z_INDEX,
                transition: 'all 0.15s ease',
              }}
              onMouseEnter={(e: React.MouseEvent<HTMLDivElement>) => {
                e.currentTarget.style.backgroundColor = hoverColor;
                e.currentTarget.style.transform = 'scale(1.3)';
              }}
              onMouseLeave={(e: React.MouseEvent<HTMLDivElement>) => {
                e.currentTarget.style.backgroundColor = baseColor;
                e.currentTarget.style.transform = 'scale(1)';
              }}
            />
          </React.Fragment>
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

