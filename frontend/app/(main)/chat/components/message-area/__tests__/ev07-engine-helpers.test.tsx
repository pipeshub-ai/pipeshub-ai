/**
 * EV-07 / CM-01: an engine's own helper (Claude Code's Agent tool, a Codex sub-agent, an opencode task) is a
 * collapsed row in the answer's timeline, named in plain words with what it was asked; its commands and tool calls
 * open under it, and the run's own steps stay beside it.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { buildActivitySummary, childNow, SubAgentGroup } from '../agent-activity';
import { engineSteps, withEngineSteps, withoutHelpers } from '../../../engine-steps';
import type { MessagePart, SSEEngineProgressEvent } from '../../../types';

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const line = (kind: string, text: string, data: Record<string, unknown>): SSEEngineProgressEvent => ({
  tool: 'code_task', taskId: 't', kind, text, data,
});

const FRAMES: SSEEngineProgressEvent[] = [
  line('command', 'pwd', { callId: 'own' }),
  line('command_done', '', { callId: 'own', exitCode: 0 }),
  line('helper', 'helper', { subSessionId: 's1', callId: 'spawn', status: 'started', task: 'List the files' }),
  line('command', 'ls', { callId: 'c1', subSessionId: 's1' }),
  line('output', 'a.md\n', { callId: 'c1', subSessionId: 's1' }),
  line('command_done', '', { callId: 'c1', exitCode: 0, subSessionId: 's1' }),
  line('tool', 'search', { callId: 'k1', subSessionId: 's1', displayName: 'Searched the knowledge base' }),
  line('tool_result', 'search', { callId: 'k1', status: 'completed', result: '2 hits' }),
  line('helper', '', { subSessionId: 's1', callId: 'spawn', status: 'completed' }),
  line('tool', 'fetch', { callId: 'k2' }),
];

const ENGINE: MessagePart = { type: 'engine_task', runId: 'r1', frames: FRAMES } as MessagePart;

describe('EV-07: an engine helper and its steps', () => {
  it('nests the helper\'s steps under it and keeps the run\'s own beside it', () => {
    const steps = engineSteps(FRAMES, false);
    expect(steps.map((s) => s.kind)).toEqual(['command', 'helper', 'tool']);
    const helper = steps[1];
    if (helper?.kind !== 'helper') throw new Error('expected a helper');
    expect(helper).toMatchObject({ status: 'completed', task: 'List the files', callId: 'spawn' });
    expect(helper.steps.map((s) => [s.kind, s.status])).toEqual([['command', 'completed'], ['tool', 'completed']]);
    expect(helper.steps[1]).toMatchObject({ result: '2 hits', ended: true });
  });

  it('a helper still open when the run ends is closed with its steps', () => {
    const open = FRAMES.slice(0, 7);
    const [, helper] = engineSteps(open, true);
    expect(helper).toMatchObject({ kind: 'helper', status: 'completed' });
    const [, running] = engineSteps(open, false);
    expect(running).toMatchObject({ kind: 'helper', status: 'running' });
  });

  it('is a sub-agent row of the timeline, its steps in its parts, without a run of its own', () => {
    const rows = withEngineSteps([ENGINE], true);
    expect(rows.map((r) => r.type)).toEqual(['engine_command', 'sub_agent', 'tool_call']);
    const helper = rows[1]!;
    expect(helper.runId).toBeUndefined();
    expect(helper.engineHelper).toEqual({ task: 'List the files' });
    expect(helper.parts?.map((p) => [p.type, p.runId])).toEqual([['engine_command', 'r1'], ['tool_call', 'r1']]);
    expect(withoutHelpers(rows).map((r) => r.type)).toEqual(['engine_command', 'engine_command', 'tool_call', 'tool_call']);
  });

  it('renders collapsed with a plain name and status, and opens to its steps', () => {
    const helper = withEngineSteps([ENGINE], true)[1]!;
    render(<Theme><SubAgentGroup part={helper} /></Theme>);
    expect(screen.getByText('Helper agent: List the files')).toBeTruthy();
    expect(screen.getByTestId('sub-agent-status').textContent).toBe(i18n.t('durableRun.child.completed'));
    expect(screen.queryByText('ls')).toBeNull();
    fireEvent.click(screen.getByText('Helper agent: List the files'));
    expect(screen.getByText(/ls/)).toBeTruthy();
    expect(document.body.textContent).not.toContain('s1');
  });

  it('a helper the engine gave no task is "Helper agent"', () => {
    render(<Theme><SubAgentGroup part={{ type: 'sub_agent', status: 'running', engineHelper: {}, parts: [] }} /></Theme>);
    expect(screen.getByText('Helper agent')).toBeTruthy();
  });

  it('the summary counts the helper\'s steps and the helper; "now" reads into it', () => {
    const t = i18n.t.bind(i18n);
    const summary = buildActivitySummary([ENGINE], t);
    expect(summary).toContain(t('agentActivity.summary.commands', { count: 2 }));
    expect(summary).toContain(t('agentActivity.summary.delegated', { count: 1 }));
    const running: MessagePart = { type: 'engine_task', runId: 'r1', frames: FRAMES.slice(0, 4) } as MessagePart;
    expect(childNow([running], t)).toContain('ls');
  });
});
