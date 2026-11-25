/**
 * UXR-B1 B1-20: the durable stream's `plan` frame reaches `onPlan`, a child
 * run's never does, and a resumed stream restores the last plan.
 */
import { describe, it, expect } from 'vitest';
import { createAGUIEventHandler, planOf, type AGUIHandlerCheckpoint } from '../agui-event-handler';
import type { StreamMessageCallbacks } from '../api';
import type { SSEPlanEvent } from '../types';
// Static: the store's module graph is large, and importing it inside the test ran into its 5 s timeout under load.
import { useChatStore } from '../store';
import { planStreamCallbacks } from '../plan-stream';

const step = (taskId: string, status: string) => ({ taskId, title: `Step ${taskId}`, status, deps: [] });
const frame = (value: unknown) => ({ event: 'message', data: { type: 'CUSTOM', name: 'plan', value } });
const started = (runId: string, parentRunId?: string) => ({
  event: 'message',
  data: { type: 'RUN_STARTED', threadId: 's', runId, ...(parentRunId ? { parentRunId } : {}) },
});

function viewer(): { callbacks: StreamMessageCallbacks; plans: SSEPlanEvent[] } {
  const plans: SSEPlanEvent[] = [];
  return { callbacks: { onPlan: (plan) => plans.push(plan) }, plans };
}

describe('UXR-B1: the plan frame', () => {
  it('reaches onPlan as the whole plan, the latest replacing the last', () => {
    const { callbacks, plans } = viewer();
    const handle = createAGUIEventHandler(callbacks);
    handle(started('run-1'));
    handle(frame({ runId: 'run-1', steps: [step('t1', 'in_progress')], currentTaskId: 't1' }));
    handle(frame({ runId: 'run-1', steps: [step('t1', 'completed'), step('t2', 'in_progress')], currentTaskId: 't2' }));
    expect(plans.map((p) => p.steps.map((s) => s.status))).toEqual([['in_progress'], ['completed', 'in_progress']]);
    expect(plans[1]?.currentTaskId).toBe('t2');
  });

  it("never takes a sub-agent's plan for the run's", () => {
    const { callbacks, plans } = viewer();
    const handle = createAGUIEventHandler(callbacks);
    handle(started('run-1'));
    handle(started('child-1', 'run-1'));
    handle(frame({ runId: 'child-1', steps: [step('c1', 'pending')], currentTaskId: null }));
    expect(plans).toEqual([]);
  });

  it('a resumed stream shows the plan it had, without the frame again', () => {
    const first = viewer();
    const before = createAGUIEventHandler(first.callbacks);
    before(started('run-1'));
    before(frame({ runId: 'run-1', steps: [step('t1', 'in_progress')], currentTaskId: 't1' }));
    const saved = JSON.parse(JSON.stringify(before.checkpoint())) as AGUIHandlerCheckpoint;
    const second = viewer();
    createAGUIEventHandler(second.callbacks, undefined, { resumeFrom: saved }).publishView();
    expect(second.plans).toEqual(first.plans);
  });

  it('drops malformed steps and never guesses a shape', () => {
    expect(planOf({ runId: 'r', steps: [step('t1', 'exploded'), { title: 'x' }, step('t2', 'pending')] })).toEqual({
      runId: 'r',
      steps: [step('t2', 'pending')],
      currentTaskId: null,
    });
    expect(planOf({ steps: [] })).toBeNull();
    expect(planOf('plan')).toBeNull();
  });
});

describe('UXR-B1: the slot keeps the plan', () => {
  it("holds the latest plan, and none while the slot is on another run", () => {
    const slotId = useChatStore.getState().createSlot(null);
    const plan: SSEPlanEvent = { runId: 'run-1', steps: [step('t1', 'in_progress') as never], currentTaskId: 't1' };
    planStreamCallbacks(slotId, () => false).onPlan?.(plan);
    expect(useChatStore.getState().slots[slotId]?.plan).toBeUndefined();
    planStreamCallbacks(slotId).onPlan?.(plan);
    expect(useChatStore.getState().slots[slotId]?.plan).toEqual(plan);
  });
});
