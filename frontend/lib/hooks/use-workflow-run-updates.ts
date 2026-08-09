'use client';

import { useEffect, useRef } from 'react';
import { subscribeToNotificationEvent } from '@/lib/socket/notification-socket';

export interface WorkflowRunUpdate {
  workflowId: string;
  runId: string;
  status: string;
  redirectLink?: string;
  conversationId?: string;
  outputSummary?: string;
  triggerKind?: string;
  workflowName?: string;
  userId?: string;
  orgId?: string;
}

/**
 * Subscribe to `workflowRunUpdate` socket events emitted by the Node.js
 * notification service whenever a workflow run transitions state (started,
 * succeeded, failed, awaiting_input).
 *
 * The subscription is registered with the socket module rather than with a
 * socket instance, so it survives mounting before the socket connects and
 * survives the teardown/rebuild that a token refresh performs.
 *
 * `handler` may be a fresh closure on every render without causing a
 * resubscribe.
 *
 * Usage:
 *   useWorkflowRunUpdates((update) => { ... });
 */
export function useWorkflowRunUpdates(handler: (update: WorkflowRunUpdate) => void): void {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(
    () =>
      subscribeToNotificationEvent<WorkflowRunUpdate>('workflowRunUpdate', (update) =>
        handlerRef.current(update)
      ),
    []
  );
}
