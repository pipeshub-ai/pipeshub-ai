'use client';

import { Handle, Position, useNodeConnections } from '@xyflow/react';
import type { FlowNodeData } from '../types';
import { FLOW_NODE_PANEL_BG } from '../flow-theme';
import { HANDLE_CONFIG } from './node-constants';
import { calculateHandlePosition, shouldShowInputHandles, shouldShowOutputHandles } from './node-utils';

const handleRing = FLOW_NODE_PANEL_BG;

function ConnectedHandle({
  type,
  position,
  id,
  style,
}: {
  type: 'source' | 'target';
  position: Position;
  id: string;
  style: React.CSSProperties;
}) {
  const connections = useNodeConnections({ handleType: type, handleId: id });
  const isConnected = connections.length > 0;

  return (
    <Handle
      type={type}
      position={position}
      id={id}
      className="agent-builder-node-handle"
      data-connected={isConnected ? 'true' : 'false'}
      style={style}
    />
  );
}

export function NodeHandles({ data }: { data: FlowNodeData }) {
  const nodeType = data.type;
  const showIn = shouldShowInputHandles(nodeType) && data.inputs?.length;
  const showOut = shouldShowOutputHandles(nodeType) && data.outputs?.length;

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
            <ConnectedHandle
              key={`out-${output}-${index}`}
              type="source"
              position={Position.Right}
              id={output}
              style={{
                top: calculateHandlePosition(
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
          ))}
        </>
      ) : null}
    </>
  );
}
