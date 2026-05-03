'use client';

import { BaseEdge, type EdgeProps, getSmoothStepPath } from '@xyflow/react';
import { FLOW_EDGE } from '../flow-theme';

export default function CustomEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  selected,
}: EdgeProps) {
  const [edgePath] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 18,
  });

  const stroke = (style.stroke as string) || FLOW_EDGE.line;

  return (
    <BaseEdge
      id={id}
      path={edgePath}
      style={{
        ...style,
        strokeWidth: selected ? 2 : 1.5,
        stroke: selected ? FLOW_EDGE.emphasis : stroke,
        opacity: selected ? 1 : 0.94,
      }}
    />
  );
}
