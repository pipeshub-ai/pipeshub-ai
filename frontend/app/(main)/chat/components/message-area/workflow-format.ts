import type { CSSProperties } from 'react';

/**
 * Shared presentation helpers for the in-chat workflow cards
 * (`WorkflowCard`, `WorkflowRunHeader`, `PrereqCheckCard`,
 * `WorkflowUpdatedCard`). Each had its own byte-identical copy of the
 * timestamp formatter and its own border/radius/background values, so the
 * four drifted apart from each other and from every other in-message block.
 */

/** Locale-aware absolute timestamp, or null when the value isn't a date. */
export function formatWorkflowTimestamp(
  iso: string | null | undefined,
  locale: string,
): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  try {
    return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  } catch {
    return date.toLocaleString();
  }
}

export function triggerLabelKey(kind: string): string {
  switch (kind) {
    case 'cron':
    case 'interval':
      return 'workflowCard.triggerRecurring';
    case 'one_time':
      return 'workflowCard.triggerOneTime';
    case 'event':
      return 'workflowCard.triggerEvent';
    case 'webhook':
      return 'workflowCard.triggerWebhook';
    default:
      return 'workflowCard.triggerRecurring';
  }
}

/**
 * The in-message block recipe already used by the agent-activity timeline
 * (`ToolCallCard`), so a workflow card reads as part of the message rather
 * than as a floating surface pasted into it.
 */
export const workflowCardStyle: CSSProperties = {
  border: '1px solid var(--slate-4)',
  borderRadius: 'var(--radius-3)',
  backgroundColor: 'var(--slate-a2)',
  padding: 'var(--space-2) var(--space-3)',
  overflow: 'hidden',
};

/**
 * Mirrors `TERMINAL_RUN_STATUSES` in the Python domain model. A status missing
 * here leaves the run card stuck on "started" forever, so the two lists have to
 * stay in step.
 */
export const TERMINAL_RUN_STATUSES = new Set([
  'succeeded',
  'failed',
  'abandoned',
  'dlq',
  'cancelled',
]);

export type WorkflowStatusColor = 'green' | 'red' | 'orange' | 'blue' | 'gray';

export function workflowStatusColor(status: string | null | undefined): WorkflowStatusColor {
  switch (status) {
    case 'succeeded':
      return 'green';
    case 'failed':
      return 'red';
    case 'dlq':
    case 'abandoned':
      return 'orange';
    case 'running':
      return 'blue';
    default:
      return 'gray';
  }
}
