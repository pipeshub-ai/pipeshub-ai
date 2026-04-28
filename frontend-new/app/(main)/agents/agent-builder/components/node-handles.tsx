'use client';

import React from 'react';
import { Handle, Position, useNodeConnections } from '@xyflow/react';
import type { FlowNodeData } from '../types';
import { FLOW_NODE_PANEL_BG } from '../flow-theme';
import { HANDLE_CONFIG } from './node-constants';
import { calculateHandlePosition, shouldShowInputHandles, shouldShowOutputHandles } from './node-utils';

const handleRing = FLOW_NODE_PANEL_BG;

// Map output handle IDs to label + color for conditional-check nodes
const CONDITIONAL_HANDLE_META: Record<string, { label: string; color: string; hoverColor: string }> = {
  pass: { label: 'Pass', color: '#58b78a', hoverColor: '#469f77' },
  fail: { label: 'Fail', color: '#d98989', hoverColor: '#c87777' },
};

function ConnectedHandle({
  type,
  position,
  id,
  style,
  isConditional,
}: {
  type: 'source' | 'target';
  position: Position;
  id: string;
  style: React.CSSProperties;
  isConditional?: boolean;
}) {
  const connections = useNodeConnections({ handleType: type, handleId: id });
  const isConnected = connections.length > 0;
  const meta = isConditional ? CONDITIONAL_HANDLE_META[id] : undefined;
  const resolvedBackgroundColor =
    meta?.color ?? (typeof style.background === 'string' ? style.background : undefined);
  const resolvedBorderColor = meta
    ? `rgba(${parseInt(meta.color.slice(1, 3), 16)}, ${parseInt(meta.color.slice(3, 5), 16)}, ${parseInt(meta.color.slice(5, 7), 16)}, 0.35)`
    : 'var(--gray-7)';
  const handleKind = meta ? `conditional-${id}` : 'default';

  return (
    <Handle
      type={type}
      position={position}
      id={id}
      className="agent-builder-node-handle"
      data-connected={isConnected ? 'true' : 'false'}
      data-handle-kind={handleKind}
      style={{
        ...style,
        backgroundColor: resolvedBackgroundColor,
        borderColor: resolvedBorderColor,
      }}
    />
  );
}

export function NodeHandles({ data }: { data: FlowNodeData }) {
  const nodeType = data.type;
  const showIn = shouldShowInputHandles(nodeType) && data.inputs?.length;
  const showOut = shouldShowOutputHandles(nodeType) && data.outputs?.length;
  const isConditional = nodeType === 'conditional-check';

  return (
    <>
      {showIn ? (
        <>
          {data.inputs!.map((input, index) => (
            <ConnectedHandle
              key={`in-${input}-${index}`}
              type="target"
              position={Position.Left}
              id={input}
              isConditional={false}
              style={{
                top: calculateHandlePosition(
                  index,
                  data.inputs!.length,
                  HANDLE_CONFIG.INPUT.POSITION_OFFSET,
                  HANDLE_CONFIG.INPUT.POSITION_INCREMENT
                ),
                left: HANDLE_CONFIG.INPUT.OFFSET_LEFT,
                width: HANDLE_CONFIG.INPUT.SIZE,
                height: HANDLE_CONFIG.INPUT.SIZE,
                background: handleRing,
                border: '1.5px solid var(--gray-7)',
                boxShadow: `0 0 0 1px ${handleRing}, 0 1px 2px var(--gray-a4)`,
                borderRadius: '50%',
                zIndex: HANDLE_CONFIG.INPUT.Z_INDEX,
              }}
            />
          ))}
        </>
      ) : null}
      {showOut ? (
        <>
          {data.outputs!.map((output, index) => (
            <React.Fragment key={`out-${output}-${index}`}>
              <ConnectedHandle
                type="source"
                position={Position.Right}
                id={output}
                isConditional={isConditional}
                style={{
                  top: isConditional
                    ? `calc(${calculateHandlePosition(
                        index,
                        data.outputs!.length,
                        HANDLE_CONFIG.OUTPUT.POSITION_OFFSET,
                        HANDLE_CONFIG.OUTPUT.POSITION_INCREMENT
                      )} + 14px)`
                    : calculateHandlePosition(
                        index,
                        data.outputs!.length,
                        HANDLE_CONFIG.OUTPUT.POSITION_OFFSET,
                        HANDLE_CONFIG.OUTPUT.POSITION_INCREMENT
                      ),
                  right: HANDLE_CONFIG.OUTPUT.OFFSET_RIGHT,
                  width: HANDLE_CONFIG.OUTPUT.SIZE,
                  height: HANDLE_CONFIG.OUTPUT.SIZE,
                  background: handleRing,
                  border: '1.5px solid var(--gray-7)',
                  boxShadow: `0 0 0 1px ${handleRing}, 0 1px 2px var(--gray-a4)`,
                  borderRadius: '50%',
                  zIndex: HANDLE_CONFIG.OUTPUT.Z_INDEX,
                }}
              />
            </React.Fragment>
          ))}
        </>
      ) : null}
    </>
  );
}
