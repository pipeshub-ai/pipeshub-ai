import { describe, expect, it } from 'vitest';
import type { ActivityTool, MessagePart, SSEEngineProgressEvent } from '../../../types';
import { engineSteps, withEngineSteps } from '../../../engine-steps';
import { describeTool } from '../../../utils/tool-display';
import { engineRunTools, stepItems } from '../activity-steps';
import { engineJob } from '@/lib/harness-jobs/engine-worker';
import { toolLabel } from '@/lib/harness-jobs/step-titles';
import { words } from '@/lib/harness-jobs/__tests__/fixtures';
import fixture from './data/fu5-opencode-ticket-frames.json';

/**
 * FIX-TOOLWORDS (QA-AUTO FU-5): the durable lane's opencode job filed a ticket through the scripted SaaS MCP server,
 * and its Activity showed "Used Tools mcp e2esaas create ticket" as a live step beside the approval for that call.
 * The frames are the lane's (`recorded`) and what the worker journals now (`fixed`): the gateway's name for the tool,
 * its display name, and the ask naming the step it gates.
 */

const recorded = fixture.recorded as SSEEngineProgressEvent[];
const fixed = fixture.fixed as SSEEngineProgressEvent[];
const ASK = 'ix.approve.kaaa8862305c13d14f9b79ade140095fd8bc8587f';

const held = (status: ActivityTool['status'], extra: Partial<ActivityTool> = {}): ActivityTool => ({
  toolCallId: ASK,
  name: 'mcp_e2esaas_create_ticket',
  displayName: 'E2Esaas · create_ticket',
  status,
  ...extra,
});

const ownParts = (frames: SSEEngineProgressEvent[], ended = false): MessagePart[] =>
  withEngineSteps([{ type: 'engine_task', runId: 'job', frames }], ended);

const resultOf = (status: 'completed' | 'failed'): SSEEngineProgressEvent => ({
  ...fixed[1]!,
  kind: 'tool_result',
  data: { callId: 'call_e2e_33', status, result: 'filed' },
});

describe('FIX-TOOLWORDS: a gateway call that waits for an ask is one step', () => {
  it('the recorded frames named the step in raw words, which is why the worker now journals the gateway name', () => {
    const [step] = engineSteps(recorded, false);
    expect(step).toMatchObject({ kind: 'tool', toolName: 'tools_mcp_e2esaas_create_ticket' });
    expect(describeTool(step!.kind === 'tool' ? step!.toolName : '').displayName).toBe('Tools MCP e2esaas create ticket');
  });

  it('the ask names its step, and the step carries it to the timeline row', () => {
    const steps = engineSteps(fixed, false);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ kind: 'tool', callId: 'call_e2e_33', interactionId: ASK, status: 'running' });
    expect(ownParts(fixed)[0]).toMatchObject({ type: 'tool_call', toolCallId: 'call_e2e_33', interactionId: ASK });
  });

  it('an ask the worker journaled before the step it gates still names that step (the lane\'s saved order)', () => {
    const saved = [fixed[0]!, fixed[2]!, fixed[1]!];
    expect(engineSteps(saved, false)).toEqual([expect.objectContaining({ kind: 'tool', callId: 'call_e2e_33', interactionId: ASK })]);
    expect(engineRunTools(ownParts(saved), [held('awaiting_approval')])).toHaveLength(1);
    const job = engineJob({ runId: 'job', engine: 'opencode', status: 'suspended', frames: saved }, words);
    expect(job.worker.steps.map((s) => s.kind)).toEqual(['approval']);
    expect(job.state.done).toBe(0);
  });

  it('the classic Activity panel draws one step that waits for the person, in plain words', () => {
    const tools = engineRunTools(ownParts(fixed), [held('awaiting_approval')]);
    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({ toolCallId: 'call_e2e_33', status: 'awaiting_approval' });
    expect(describeTool(tools[0]!.name, tools[0]!.displayName).displayName).toBe('E2Esaas · Create ticket');
    expect(stepItems(tools)).toHaveLength(1);
  });

  it('once allowed, the same step runs and ends with its result and the answer', () => {
    const tools = engineRunTools(ownParts([...fixed, resultOf('completed')], true), [held('completed', { approvalAnswer: 'allowed' })]);
    expect(tools).toEqual([expect.objectContaining({ toolCallId: 'call_e2e_33', status: 'completed', approvalAnswer: 'allowed', resultPreview: 'filed' })]);
  });

  it('a refused ask is the step, refused', () => {
    const tools = engineRunTools(ownParts(fixed, true), [held('denied', { approvalAnswer: 'declined' })]);
    expect(tools).toEqual([expect.objectContaining({ toolCallId: 'call_e2e_33', status: 'denied', approvalAnswer: 'declined' })]);
  });

  it('an ask that names no step still stands alone, after the steps', () => {
    const unlinked = fixed.map((f) => (f.kind === 'approval' ? { ...f, data: { ...f.data, callId: undefined } } : f));
    const tools = engineRunTools(ownParts(unlinked), [held('awaiting_approval')]);
    expect(tools.map((t) => t.toolCallId)).toEqual(['call_e2e_33', ASK]);
  });

  it('the job view parks on the ask itself: it is the live row, with no running step before it', () => {
    const job = engineJob({ runId: 'job', engine: 'opencode', status: 'suspended', frames: fixed }, words);
    expect(job.status).toBe('needsYou');
    expect(job.worker.steps.map((s) => s.kind)).toEqual(['approval']);
    expect(job.state.done).toBe(0);
    expect(job.worker.steps[0]!.title).not.toMatch(/mcp|tools|_/i);
  });

  it('answered, the job keeps the one row, stamped', () => {
    const job = engineJob(
      { runId: 'job', engine: 'opencode', status: 'completed', frames: [...fixed, resultOf('completed')], approvals: [{ interactionId: ASK, toolName: 'mcp_e2esaas_create_ticket', displayName: 'E2Esaas · create_ticket', status: 'approved' }] },
      words,
    );
    expect(job.worker.steps.map((s) => s.kind)).toEqual(['approval']);
    expect(job.state.answers[ASK]).toBe('yes');
  });

  it("a gateway tool's label is the chat's words, never its id", () => {
    expect(toolLabel('mcp_e2esaas_create_ticket')).toBe('Create ticket');
    expect(toolLabel('mcp_e2esaas_create_ticket', 'E2Esaas · create_ticket')).toBe('Create ticket');
    expect(toolLabel('x', 'Send a Slack message')).toBe('Send a Slack message');
  });
});
