/**
 * UX5 RD-7 (QA-11): every example of the shared token vector, put in each place the
 * activity panel shows server text (a command, its streamed and tailed output, a tool
 * call's name, refusal rule and approval reason, a task, a file and its diff), never
 * reaches the screen; the vector's mask does.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { DurableAgentRunHandle } from '@/chat/types';
import { reduceActivity, toActivityEvent, toActivityView } from '@/chat/activity';
import { VECTOR_EXAMPLES, VECTOR_MASK } from '@/chat/__tests__/token-vector';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
  streamSSEGet: vi.fn(),
}));

import { ActivityPanelView } from '../activity-panel/activity-panel';
import { ToolCallCard } from '../message-area/agent-activity';

const RUN: DurableAgentRunHandle = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };

afterEach(cleanup);

function snapshot(secret: string): unknown {
  return {
    runId: RUN.runId,
    status: 'running',
    engine: true,
    commands: [
      {
        commandId: 'cmd-1',
        source: 'engine',
        command: `curl -H 'auth: ${secret}' https://x`,
        status: 'completed',
        exitCode: 0,
        startedAt: 1,
        durationMs: 5,
        stdout: `token=${secret}\n`,
        stderr: `${secret}\n`,
        truncated: false,
        liveDropped: 0,
        hasLog: false,
      },
      { commandId: 'cmd-2', source: 'run_code', command: 'print(1)', status: 'running', exitCode: null, startedAt: 2, durationMs: null, stdout: '', stderr: '', truncated: false, liveDropped: 0, hasLog: false },
    ],
    omittedCommands: 0,
    tools: [
      {
        toolCallId: 'call-1',
        name: `send_${secret}`,
        displayName: `Send ${secret}`,
        status: 'awaiting_approval',
        reason: {
          rule: 'flow_external_write',
          sensitivity: 'private',
          sources: [{ title: `Plan ${secret}`, recordId: 'rec-1' }],
          moreSources: 0,
          destination: `#sales ${secret}`,
          conversationGrant: false,
        },
      },
      { toolCallId: 'call-2', name: 'post', status: 'blocked', refusal: { rule: `rule ${secret}`, code: secret } },
    ],
    tasks: [{ taskId: 'task-1', title: `Rotate ${secret}`, status: 'in_progress' }],
    files: [{ path: `config/${secret}.env`, change: 'modified', diff: `+KEY=${secret}\n` }],
    followups: [],
  };
}

function renderWith(secret: string) {
  let view = toActivityView(snapshot(secret));
  const output = toActivityEvent('output', { commandId: 'cmd-2', stream: 'stdout', text: `live ${secret}\n` });
  expect(view).not.toBeNull();
  expect(output).not.toBeNull();
  view = reduceActivity(view, output!);
  return render(
    <Theme>
      <ActivityPanelView run={RUN} state={{ view, connection: 'live' }} variant="pane" onClose={vi.fn()} />
    </Theme>,
  );
}

describe('UX5 RD-7: the activity panel shows no example of the token vector', () => {
  it('has examples to run (a vector without them would prove nothing)', () => {
    expect(VECTOR_EXAMPLES.length).toBeGreaterThan(10);
  });

  it.each(VECTOR_EXAMPLES.map((e) => [e.id, e.secret] as const))('%s', (_id, secret) => {
    const { container } = renderWith(secret);
    for (const button of screen.queryAllByRole('button', { expanded: false })) fireEvent.click(button);
    const text = container.textContent ?? '';
    const labels = Array.from(container.querySelectorAll('[aria-label],[title]'))
      .map((el) => `${el.getAttribute('aria-label') ?? ''} ${el.getAttribute('title') ?? ''}`)
      .join(' ');
    for (const shown of [text, labels]) {
      expect(shown).not.toContain(secret);
      for (const line of secret.split('\n').filter((l) => l.length >= 16)) expect(shown).not.toContain(line);
    }
    expect(text).toContain(VECTOR_MASK);
  });
});

describe('UX5 RD-7: a tool call in the chat timeline shows no example of the token vector (QA-11 finding)', () => {
  it.each(VECTOR_EXAMPLES.map((e) => [e.id, e.secret] as const))('%s', (_id, secret) => {
    for (const part of [
      { args: JSON.stringify({ token: secret, note: `x ${secret}` }), resultPreview: `out ${secret}` },
      { argsSummary: `query ${secret}`, resultSummary: `found ${secret}` },
    ]) {
      const { container, unmount } = render(
        <Theme>
          <ToolCallCard part={{ type: 'tool_call', toolCallId: 'call-1', toolName: 'search', status: 'completed', ...part }} />
        </Theme>,
      );
      fireEvent.click(screen.getByRole('button'));
      const text = container.textContent ?? '';
      expect(text).not.toContain(secret);
      for (const line of secret.split('\n').filter((l) => l.length >= 16)) expect(text).not.toContain(line);
      unmount();
    }
  });
});
