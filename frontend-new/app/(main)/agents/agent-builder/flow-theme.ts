/** Edge stroke — theme tokens so lines read on the dotted canvas in light and dark. */
export const FLOW_EDGE = {
  line: 'var(--agent-flow-edge)',
  emphasis: 'var(--agent-flow-edge-emphasis)',
} as const;

/** Card chrome — neutral wells; icon color follows platform gray. */
export type FlowNodeChrome = {
  iconWell: string;
  iconWellBorder: string;
  iconColor: string;
};

export function getFlowNodeChrome(_nodeType: string): FlowNodeChrome {
  return {
    iconWell: 'var(--gray-3)',
    iconWellBorder: 'var(--gray-6)',
    iconColor: 'var(--agent-flow-text)',
  };
}

/** Opaque card fill — Radix `--color-panel` may be translucent; nodes must fully occlude the graph when stacked or dragged. */
export const FLOW_NODE_PANEL_BG = 'var(--color-panel-solid)';

/** Handle fill — panel center reads cleanly on the canvas. */
export function handleAccentForId(_handleId: string): string {
  return 'var(--gray-1)';
}

export const FLOW_NODE_CARD = {
  radius: 'var(--radius-2)',
  borderIdle: '1px solid var(--agent-flow-node-border)',
  shadow: 'var(--agent-flow-card-shadow)',
  shadowSelected: 'var(--agent-flow-card-shadow-selected)',
} as const;

/** Prompt / preview wells — inset surfaces (tokens in globals.css). */
export const FLOW_NODE_WELL = {
  border: '1px solid var(--agent-flow-well-border)',
  background: 'var(--agent-flow-well-bg)',
  radius: 'var(--radius-2)',
} as const;
