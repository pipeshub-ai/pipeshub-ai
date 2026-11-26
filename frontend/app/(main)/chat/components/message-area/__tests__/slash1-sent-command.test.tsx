/**
 * SLASH1: a question sent as a /command reads as its chip and typed arguments; "What was sent" opens the text the
 * server expanded and its provenance. The run's first timeline row says which command it ran, live and saved.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import { createAGUIEventHandler } from '../../../agui-event-handler';
import { CommandsApi, toSentCommand, useCommandCatalog, type ChatCommand, type SentCommand } from '../../../commands';
import { SlashCommandMenu } from '../../chat-panel/slash-command-menu';
import { loadHistoricalMessages } from '../../../runtime';
import type { ConversationMessage, MessagePart } from '../../../types';
import { AgentActivityTimeline } from '../agent-activity';
import { ExpandableUserQuery } from '../expandable-user-query';

afterEach(() => cleanup());

const EXPANDED = 'Look up incident INC-4211 in Jira. Depth: full.';
const STORED = {
  name: 'triage',
  version: '1.0.3',
  contentDigest: `sha256:${'a'.repeat(64)}`,
  args: { incident: 'INC-4211', depth: 'full' },
  typed: '/triage INC-4211 full',
  owner: 'you',
};

describe('a question sent as a /command', () => {
  beforeEach(() => useCommandCatalog.setState({ commands: [], loadedAt: Date.now(), bodies: {} }));

  it('a reload reads the typed form and keeps what was sent', () => {
    const [user] = loadHistoricalMessages([
      { _id: 'm1', messageType: 'user_query', content: EXPANDED, command: STORED, createdAt: '2026-10-16T10:00:00Z' } as ConversationMessage,
    ]).messages;
    expect(user.content).toEqual([{ type: 'text', text: '/triage INC-4211 full' }]);
    expect((user.metadata?.custom as { command: unknown }).command).toEqual({
      name: 'triage',
      version: '1.0.3',
      args: { incident: 'INC-4211', depth: 'full' },
      typed: '/triage INC-4211 full',
      owner: 'you',
      expanded: EXPANDED,
    });
  });

  it('renders the chip and the typed arguments; "What was sent" is a disclosure with the text and its provenance', () => {
    render(
      <Theme>
        <ExpandableUserQuery
          question="/triage INC-4211 full"
          command={{ ...STORED, owner: 'you', expanded: EXPANDED }}
        />
      </Theme>,
    );
    expect(screen.getByTestId('user-command-chip').textContent).toBe('/triage');
    expect(screen.getByText('INC-4211 full')).toBeTruthy();
    const disclosure = screen.getByRole('button', { name: /What was sent/ });
    expect(disclosure.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByTestId('user-command-sent-text')).toBeNull();

    fireEvent.click(disclosure);
    expect(disclosure.getAttribute('aria-expanded')).toBe('true');
    expect(disclosure.getAttribute('aria-controls')).toBe(screen.getByTestId('user-command-sent-text').id);
    expect(screen.getByTestId('user-command-sent-text').textContent).toContain(EXPANDED);
    expect(screen.getByTestId('user-command-provenance').textContent).toBe('triage v1.0.3 · by you · You');
  });

  it('while the turn streams, the chip comes from the menu\'s list and "What was sent" waits for the server', () => {
    useCommandCatalog.setState({
      commands: [{ name: 'summarize', description: '', version: '1.0.0', owner: 'pipeshub', arguments: [] }],
    });
    render(<Theme><ExpandableUserQuery question="/summarize" isStreaming /></Theme>);
    expect(screen.getByTestId('user-command-chip').textContent).toBe('/summarize');
    expect(screen.queryByRole('button', { name: /What was sent/ })).toBeNull();
  });

  it('a reloaded /review shows what was typed and how many files it reviewed, never a "What was sent" (LC2FIX)', () => {
    const review = { ...STORED, name: 'review', version: '1.0.0', args: { focus: 'the SQL' }, typed: '/review the SQL', owner: 'pipeshub', files: 7 };
    const [user] = loadHistoricalMessages([
      { _id: 'm1', messageType: 'user_query', content: '/review the SQL', command: review, createdAt: '2026-10-16T10:00:00Z' } as ConversationMessage,
    ]).messages;
    expect(user.content).toEqual([{ type: 'text', text: '/review the SQL' }]);
    const command = (user.metadata?.custom as { command: SentCommand }).command;
    expect(command).toMatchObject({ name: 'review', files: 7 });
    expect(command.expanded).toBeUndefined();
    render(<Theme><ExpandableUserQuery question="/review the SQL" command={command} /></Theme>);
    expect(screen.getByTestId('user-command-chip').textContent).toBe('/review');
    expect(screen.getByTestId('user-command-review-files').textContent).toBe('Review of 7 changed files');
    expect(screen.queryByRole('button', { name: /What was sent/ })).toBeNull();
  });

  it('a slash that named no command stays the question as typed', () => {
    render(<Theme><ExpandableUserQuery question="/etc/hosts is broken" isStreaming /></Theme>);
    expect(screen.queryByTestId('user-command-chip')).toBeNull();
    expect(screen.getByText('/etc/hosts is broken')).toBeTruthy();
  });
});

describe('a team or org command (SLASH1 S4)', () => {
  const TEAM = { ...STORED, owner: 'team', authorId: 'u-asha', teamId: 'ops' };

  it('a message keeps the author and team as ids; "What was sent" reads their names when opened', async () => {
    const provenance = vi
      .spyOn(CommandsApi, 'provenance')
      .mockResolvedValue({ owner: 'team', author: 'Asha Rao', team: 'Ops' });
    const [user] = loadHistoricalMessages([
      { _id: 'm1', messageType: 'user_query', content: EXPANDED, command: TEAM, createdAt: '2026-10-16T10:00:00Z' } as ConversationMessage,
    ]).messages;
    const command = (user.metadata?.custom as { command: SentCommand }).command;
    expect(command).toMatchObject({ owner: 'team', authorId: 'u-asha', teamId: 'ops' });
    expect(JSON.stringify(command)).not.toContain('Asha');

    render(<Theme><ExpandableUserQuery question="/triage INC-4211 full" command={command} /></Theme>);
    expect(provenance).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /What was sent/ }));
    await waitFor(() =>
      expect(screen.getByTestId('user-command-provenance').textContent).toBe('triage v1.0.3 · by Asha Rao · team Ops'),
    );
    expect(provenance).toHaveBeenCalledWith(expect.objectContaining({ authorId: 'u-asha', teamId: 'ops' }));
    provenance.mockRestore();
  });

  it('an author who is gone reads "by a former member"', async () => {
    const provenance = vi.spyOn(CommandsApi, 'provenance').mockResolvedValue({ owner: 'org', authorFormer: true });
    const command = toSentCommand({ ...STORED, owner: 'org', authorId: 'u-left' }, EXPANDED) as SentCommand;
    render(<Theme><ExpandableUserQuery question="/triage INC-4211 full" command={command} /></Theme>);
    fireEvent.click(screen.getByRole('button', { name: /What was sent/ }));
    await waitFor(() =>
      expect(screen.getByTestId('user-command-provenance').textContent).toBe('triage v1.0.3 · by a former member · Org'),
    );
    provenance.mockRestore();
  });

  it('the / menu shows the owner badge and, for a shared command, its author and team', () => {
    const commands: ChatCommand[] = [
      { name: 'triage', description: 'Triage', version: '1.0.3', owner: 'team', author: 'Asha Rao', team: 'Ops', arguments: [] },
      { name: 'mine', description: 'Mine', version: '1.0.0', owner: 'you', arguments: [] },
    ];
    render(
      <Theme>
        <SlashCommandMenu
          open
          entries={commands.map((command) => ({ kind: 'user' as const, id: `user-${command.name}`, group: command.owner === 'pipeshub' ? ('builtin' as const) : command.owner, command }))}
          highlighted={0}
          onHighlight={() => undefined}
          onSelect={() => undefined}
          onDismiss={() => undefined}
          inputRef={{ current: null }}
        />
      </Theme>,
    );
    const rows = screen.getAllByRole('option');
    expect(rows[0].textContent).toContain('Team');
    expect(rows[0].textContent).toContain('by Asha Rao · team Ops');
    expect(rows[1].textContent).toContain('You');
    expect(screen.getAllByTestId('slash-command-shared-by')).toHaveLength(1);
  });
});

describe('the run\'s first timeline row', () => {
  const PART: MessagePart = {
    type: 'slash_command',
    commandName: 'triage',
    commandVersion: '1.0.3',
    expandedChars: EXPANDED.length,
    owner: 'you',
    runId: 'run-1',
  };

  it('the live frame puts it first, once', () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    handle({ id: 'a', event: 'CUSTOM', data: { type: 'CUSTOM', name: 'notice', value: { code: 'mcp_unavailable' }, runId: 'run-1' } });
    const frame = {
      type: 'CUSTOM',
      name: 'slash_command',
      value: { name: 'triage', version: '1.0.3', expandedChars: EXPANDED.length, owner: 'you' },
      runId: 'run-1',
    };
    handle({ id: 'b', event: 'CUSTOM', data: frame });
    handle({ id: 'c', event: 'CUSTOM', data: frame });
    expect(parts.filter((p) => p.type === 'slash_command')).toEqual([PART]);
    expect(parts[0]).toEqual(PART);
  });

  it('reads "Ran /triage (v1.0.3)"', () => {
    render(<Theme><AgentActivityTimeline parts={[PART, { type: 'text', content: 'Done.', isFinal: true }]} /></Theme>);
    expect(screen.getByTestId('slash-command-step').textContent).toBe('Ran /triage (v1.0.3)');
  });
});
