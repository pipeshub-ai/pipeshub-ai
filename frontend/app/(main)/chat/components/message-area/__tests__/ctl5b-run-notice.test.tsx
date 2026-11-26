/**
 * CTL-5b: when a default-chat Agent-mode run proceeds without the person's MCP servers, the answer says so once,
 * live (the projector's `notice` frame) and after a reload (the saved `notice` part).
 */
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { createAGUIEventHandler } from '../../../agui-event-handler';
import type { MessagePart } from '../../../types';
import { getVisibleRootParts, RunNotices } from '../agent-activity';

afterEach(() => cleanup());

const TEXT =
  "Some of your MCP servers didn't respond or have no approved tools, so this answer couldn't use them. Check them under MCP servers, then try again.";
const NOTICE: MessagePart = { type: 'notice', code: 'mcp_unavailable', runId: 'run-1' };

describe('CTL-5b: the MCP notice on an answer', () => {
  it('a saved notice part shows once, in words, on the answer, never folded away with its steps', () => {
    const parts: MessagePart[] = [NOTICE, { ...NOTICE }, { type: 'text', content: 'Done.', isFinal: true }];
    expect(getVisibleRootParts(parts, false)).toEqual([]);
    render(<Theme><RunNotices parts={parts} /></Theme>);
    expect(screen.getAllByText(TEXT)).toHaveLength(1);
  });

  it('the live notice frame adds the part once, however often it is replayed', () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    const frame = { type: 'CUSTOM', name: 'notice', value: { code: 'mcp_unavailable' }, runId: 'run-1' };
    handle({ id: 'a', event: 'CUSTOM', data: frame });
    handle({ id: 'b', event: 'CUSTOM', data: frame });
    expect(parts.filter((part) => part.type === 'notice')).toEqual([NOTICE]);
  });

  it('QA23-F1: each item the run went on without is named once, with where to connect it', () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    const jira = { code: 'credential_missing', item: 'Jira', kind: 'toolset' };
    const github = { code: 'credential_missing', item: 'GitHub', kind: 'mcp' };
    for (const [id, value] of [['a', jira], ['b', github], ['c', jira]] as const) {
      handle({ id, event: 'CUSTOM', data: { type: 'CUSTOM', name: 'notice', value, runId: 'run-1' } });
    }
    expect(parts.filter((part) => part.type === 'notice')).toEqual([
      { type: 'notice', ...jira, runId: 'run-1' },
      { type: 'notice', ...github, runId: 'run-1' },
    ]);
    render(<Theme><RunNotices parts={[...parts, { type: 'notice', ...jira }]} /></Theme>);
    expect(screen.getAllByRole('note').map((note) => note.textContent)).toEqual([
      "Jira isn't connected for your account, so this answer couldn't use it. Connect it under Actions, then ask again. Connect Jira",
      "GitHub isn't connected for your account, so this answer couldn't use it. Connect it under MCP servers, then ask again. Connect GitHub",
    ]);
  });

  it('WS-09: each unconnected item links to the page where the person connects it', () => {
    render(
      <Theme>
        <RunNotices
          parts={[
            { type: 'notice', code: 'credential_missing', item: 'Jira', kind: 'toolset' },
            { type: 'notice', code: 'credential_missing', item: 'GitHub', kind: 'mcp' },
          ]}
        />
      </Theme>,
    );
    expect(screen.getByRole('link', { name: 'Connect Jira' }).getAttribute('href')).toBe('/workspace/actions');
    expect(screen.getByRole('link', { name: 'Connect GitHub' }).getAttribute('href')).toBe('/workspace/mcp-servers/personal');
  });

  it('a code this build does not know shows nothing', () => {
    render(<Theme><RunNotices parts={[{ type: 'notice', code: 'something_new' }]} /></Theme>);
    expect(screen.queryByText(TEXT)).toBeNull();
  });
});
