// Custom Edge Component for Premium Styling
import React from 'react';
import { BaseEdge, EdgeProps, getSmoothStepPath, useReactFlow } from '@xyflow/react';
import { useTheme, alpha } from '@mui/material';

const CustomEdge: React.FC<EdgeProps> = ({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  markerEnd,
  selected,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const { deleteElements } = useReactFlow();

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 16,
  });

  const edgeColor = isDark ? '#4a4a4a' : '#d0d0d0';
  const selectedColor = isDark ? '#6b6b6b' : '#8b8b8b';

  return (
    <BaseEdge
      id={id}
      path={edgePath}
      style={{
        ...style,
        strokeWidth: selected ? 2 : 1.5,
        stroke: selected ? selectedColor : (style.stroke || edgeColor),
        transition: 'all 0.2s ease',
      }}
    />
  );
};

export default CustomEdge;

