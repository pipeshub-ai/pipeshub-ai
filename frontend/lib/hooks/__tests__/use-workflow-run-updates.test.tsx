import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';
import { useWorkflowRunUpdates, type WorkflowRunUpdate } from '../use-workflow-run-updates';

/** Stands in for the socket module: records subscribers, lets tests emit. */
const subscribers = new Map<string, Set<(payload: unknown) => void>>();
let subscribeCalls = 0;

vi.mock('@/lib/socket/notification-socket', () => ({
  subscribeToNotificationEvent: (event: string, handler: (payload: unknown) => void) => {
    subscribeCalls += 1;
    if (!subscribers.has(event)) subscribers.set(event, new Set());
    subscribers.get(event)!.add(handler);
    return () => subscribers.get(event)?.delete(handler);
  },
}));

function emit(update: Partial<WorkflowRunUpdate>) {
  act(() => {
    subscribers.get('workflowRunUpdate')?.forEach((handler) => handler(update));
  });
}

function Harness({ onUpdate }: { onUpdate: (u: WorkflowRunUpdate) => void }) {
  useWorkflowRunUpdates(onUpdate);
  return React.createElement('div');
}

beforeEach(() => {
  subscribers.clear();
  subscribeCalls = 0;
});

afterEach(cleanup);

describe('useWorkflowRunUpdates', () => {
  it('delivers run updates to the handler', () => {
    const onUpdate = vi.fn();
    render(React.createElement(Harness, { onUpdate }));

    emit({ workflowId: 'wf-1', runId: 'run-1', status: 'succeeded' });

    expect(onUpdate).toHaveBeenCalledWith({
      workflowId: 'wf-1',
      runId: 'run-1',
      status: 'succeeded',
    });
  });

  it('does not resubscribe when the caller passes a fresh closure each render', () => {
    // Callers write `useWorkflowRunUpdates((u) => ...)` inline; resubscribing
    // on every render would tear the listener down mid-run.
    const { rerender } = render(React.createElement(Harness, { onUpdate: vi.fn() }));
    rerender(React.createElement(Harness, { onUpdate: vi.fn() }));
    rerender(React.createElement(Harness, { onUpdate: vi.fn() }));

    expect(subscribeCalls).toBe(1);
  });

  it('uses the latest handler rather than the one captured at subscribe time', () => {
    const stale = vi.fn();
    const fresh = vi.fn();
    const { rerender } = render(React.createElement(Harness, { onUpdate: stale }));
    rerender(React.createElement(Harness, { onUpdate: fresh }));

    emit({ workflowId: 'wf-1', runId: 'run-1', status: 'failed' });

    expect(stale).not.toHaveBeenCalled();
    expect(fresh).toHaveBeenCalledOnce();
  });

  it('unsubscribes on unmount', () => {
    const onUpdate = vi.fn();
    const { unmount } = render(React.createElement(Harness, { onUpdate }));

    unmount();
    emit({ workflowId: 'wf-1', runId: 'run-1', status: 'succeeded' });

    expect(onUpdate).not.toHaveBeenCalled();
  });
});
