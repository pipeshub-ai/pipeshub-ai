/**
 * EU-7 (ERR-UX): a failed call's card and the owner's activity row show the code's words naming the tool; the
 * card never shows the detail, the activity row shows it to the owner.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { DurableAgentRunHandle } from '@/chat/types';
import { toActivityView } from '@/chat/activity';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
  streamSSEGet: vi.fn(),
}));

import { ActivityPanelView } from '../activity-panel/activity-panel';
import { ToolCallCard } from '../message-area/agent-activity';

const RUN: DurableAgentRunHandle = { agentId: 'agent-1', sessionId: 'conv-1', runId: 'run-1' };
const DETAIL = 'ConnectError: All connection attempts failed (bolt://10.0.0.7:7687)';

afterEach(cleanup);

describe('EU-7: a failed tool call in the chat', () => {
  it('shows the code\'s words, "nothing was sent" for a write, and no detail even expanded', () => {
    const { container } = render(
      <Theme>
        <ToolCallCard
          part={{
            type: 'tool_call',
            toolCallId: 'c1',
            toolName: 'slack__send_message',
            status: 'failed',
            errorCode: 'unavailable',
            nothingSent: true,
            resultPreview: '',
          }}
        />
      </Theme>,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByTestId('tool-failure').textContent).toBe('Slack could not be reached. Nothing was sent.');
    expect(container.textContent).not.toContain('ConnectError');
  });

  it('an older saved failure with only its raw preview reads "failed", never the preview', () => {
    const { container } = render(
      <Theme>
        <ToolCallCard part={{ type: 'tool_call', toolCallId: 'c1', toolName: 'graph_lookup', status: 'failed', resultPreview: DETAIL }} />
      </Theme>,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByTestId('tool-failure').textContent).toBe('Graph lookup failed.');
    expect(container.textContent).not.toContain('ConnectError');
  });
});

describe('EU-7: the owner\'s activity panel', () => {
  it('names the failure by its code and shows the redacted detail', () => {
    const view = toActivityView({
      runId: RUN.runId,
      status: 'running',
      engine: false,
      commands: [],
      omittedCommands: 0,
      tools: [{ toolCallId: 'c1', name: 'graph_lookup', status: 'failed', failure: { code: 'timeout', detail: DETAIL } }],
      tasks: [],
      files: [],
      followups: [],
    });
    render(
      <Theme>
        <ActivityPanelView run={RUN} state={{ view, connection: 'live' }} variant="pane" onClose={vi.fn()} />
      </Theme>,
    );
    expect(screen.getByTestId('activity-tool-failure').textContent).toBe('Graph lookup did not answer in time.');
    // ACTRESULTS: the step reads in two lines; the model's detail is one click away.
    expect(screen.queryByTestId('activity-tool-detail')).toBeNull();
    fireEvent.click(screen.getByTestId('activity-tool-toggle'));
    expect(screen.getByTestId('activity-tool-detail').textContent).toContain('All connection attempts failed');
  });
});
