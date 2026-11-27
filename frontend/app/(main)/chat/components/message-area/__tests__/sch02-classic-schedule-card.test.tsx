/**
 * ADR-SCH-02 D5: on classic chat, PipesHub's own `schedule_create` (source `platform`) asks on classic chat's card in
 * the same plain words as the harness's card, through one shared formatter, with Allow and Deny only. A tool from an
 * MCP server or toolset that is also called `schedule_create` is never dressed as one.
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { PendingApproval, ToolApprovalDetails } from '../../../types';

process.env.TZ = 'America/Los_Angeles';

const chat = vi.hoisted(() => ({
  streamMessageForSlot: vi.fn(),
  buildStreamChatRequestForSlot: vi.fn((slotId: string, query: string) => ({ slotId, query })),
  state: {
    activeSlotId: 'slot-1',
    slots: { 'slot-1': { isOwner: true as boolean | null, isStreaming: false } },
    settings: { availableModels: [] },
  },
}));
const words = vi.hoisted(() => ({ calls: [] as string[] }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('../../../streaming', () => ({ streamMessageForSlot: chat.streamMessageForSlot }));
vi.mock('../../../runtime', () => ({ buildStreamChatRequestForSlot: chat.buildStreamChatRequestForSlot }));
vi.mock('../../../store', () => ({
  useChatStore: Object.assign(
    (selector: (state: typeof chat.state) => unknown) => selector(chat.state),
    { getState: () => chat.state },
  ),
}));
vi.mock('@/app/(main)/workspace/schedules/schedule-words', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/app/(main)/workspace/schedules/schedule-words')>();
  return {
    ...actual,
    scheduleCallWords: (...args: Parameters<typeof actual.scheduleCallWords>) => {
      words.calls.push(args[1].when.kind);
      return actual.scheduleCallWords(...args);
    },
  };
});

import { ToolApprovalCard, ToolApprovalReceipts } from '../tool-approval-card';
import { ApprovalCard } from '../approval-card';
import { settledToolApprovals, toolApprovalDetails } from '../../../tool-approval';

const plain = (text: string | null | undefined) => (text ?? '').replace(/[  ]/g, ' ');

const ONE_OFF_AT = () => new Date(Date.now() + 5 * 60_000);
const CRON = { name: 'Jira digest', ask: 'Summarise my open Jira issues.', when: { kind: 'cron', cron: '0 9 * * 1-5', timeZone: 'America/Los_Angeles' }, notify: ['in_app', 'email'] };

function classic(args: Record<string, unknown>, over: Partial<ToolApprovalDetails> = {}): ToolApprovalDetails {
  return {
    approvalId: 'ap-1',
    instanceId: '',
    serverName: 'PipesHub',
    toolName: 'schedule_create',
    toolTitle: 'Create a schedule',
    source: 'platform',
    kind: 'write',
    canRemember: true,
    expiresAt: Date.now() + 15 * 60_000,
    arguments: args,
    ...over,
  };
}

const renderClassic = (approval: ToolApprovalDetails, isLatest = true) =>
  render(<Theme><ToolApprovalCard approval={approval} isLatest={isLatest} rowId="row-1" /></Theme>);

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
beforeEach(() => {
  words.calls = [];
  chat.state.slots['slot-1'].isOwner = true;
  chat.state.slots['slot-1'].isStreaming = false;
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('classic chat’s card for schedule_create', () => {
  it('a one-off: "Set up a schedule", its name, "Once, today at …" and the ask, no raw arguments', () => {
    const at = ONE_OFF_AT();
    const sameDay = at.toDateString() === new Date().toDateString();
    const { container } = renderClassic(
      classic({ name: 'Joke in five minutes', ask: 'Tell me a short, clean joke.', when: { kind: 'once', at: at.toISOString(), timeZone: 'America/Los_Angeles' } }),
    );
    expect(screen.getByText('Set up a schedule')).toBeTruthy();
    const summary = screen.getByTestId('schedule-summary');
    expect(summary.textContent).toContain('Joke in five minutes');
    expect(summary.textContent).toContain('Each run is asked');
    expect(summary.textContent).toContain('Tell me a short, clean joke.');
    expect(summary.textContent).toContain('Workspace > Schedules');
    expect(plain(screen.getByTestId('schedule-when').textContent)).toMatch(sameDay ? /^Once, today at \d{1,2}:\d{2} (AM|PM)$/ : /^Once, tomorrow at /);
    expect(container.textContent).not.toContain('{');
    expect(container.textContent).not.toContain('PipesHub');
  });

  it('a cron: "Every weekday at 9:00 AM (America/Los_Angeles)" and its notices', () => {
    renderClassic(classic(CRON));
    expect(plain(screen.getByTestId('schedule-when').textContent)).toBe('Every weekday at 9:00 AM (America/Los_Angeles)');
    expect(screen.getByTestId('schedule-summary').textContent).toContain('In PipesHub, Email');
  });

  it('offers Allow and Deny only, and never sends "remember", even where the server would allow it', () => {
    renderClassic(classic(CRON, { canRemember: true }));
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByText("Don't ask again for this tool")).toBeNull();
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Allow', 'Deny']);
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }));
    expect(chat.buildStreamChatRequestForSlot).toHaveBeenCalledWith('slot-1', 'Tool approval: allow', undefined, {
      approvalId: 'ap-1',
      decision: 'allow',
    });
  });

  it('arguments that are not a schedule are listed as any call’s', () => {
    renderClassic(classic({ name: 'n', when: 'tomorrow' }));
    expect(screen.queryByTestId('schedule-summary')).toBeNull();
    expect(screen.getByText('tomorrow')).toBeTruthy();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('its one-line notices and receipts name the schedule, not the tool', () => {
    renderClassic(classic(CRON), false);
    expect(screen.getByTestId('tool-approval-card').textContent).toBe("The schedule wasn't approved, so it wasn't set up.");
    cleanup();
    const parts = [{ type: 'tool_call', toolCallId: 'c', status: 'completed', approved: true, approvalAnswer: 'allowed', approval: classic(CRON) }];
    const settled = settledToolApprovals(parts as never);
    render(<Theme><ToolApprovalReceipts items={settled} /></Theme>);
    expect(screen.getByTestId('tool-approval-receipts').textContent).toBe('You allowed the schedule');
  });

  it('the wire frame’s source is kept, so the card can tell PipesHub’s tool from a look-alike', () => {
    expect(toolApprovalDetails({ ...classic(CRON) })?.source).toBe('platform');
    expect(toolApprovalDetails({ ...classic(CRON), source: undefined })).not.toHaveProperty('source');
  });
});

describe('a look-alike is not dressed as a schedule', () => {
  it.each([
    ['an MCP server’s tool', { source: 'mcp', serverName: 'Evil MCP' }],
    ['a toolset’s tool', { source: 'toolset', serverName: 'Calendar' }],
    ['a frame without a source', { source: undefined, serverName: 'Unknown' }],
  ])('%s called schedule_create keeps the ordinary card', (_name, over) => {
    renderClassic(classic(CRON, { ...over, toolTitle: null }));
    expect(screen.queryByTestId('schedule-summary')).toBeNull();
    expect(screen.queryByText('Set up a schedule')).toBeNull();
    expect(screen.getByText(over.serverName)).toBeTruthy();
    // Its own rules: "Don't ask again" stays where the server offers it.
    expect(screen.getByRole('checkbox', { name: "Don't ask again for this tool" })).toBeTruthy();
    expect(words.calls).toEqual([]);
  });

  it('PipesHub’s name on another tool is not one either', () => {
    renderClassic(classic(CRON, { toolName: 'mcp_evil_schedule_create' }));
    expect(screen.queryByTestId('schedule-summary')).toBeNull();
  });
});

describe('one formatter for both cards', () => {
  it('the harness card and classic chat’s card say the same schedule in the same words, from the same formatter', () => {
    renderClassic(classic(CRON));
    const classicSummary = screen.getByTestId('schedule-summary').textContent;
    cleanup();
    const held: PendingApproval = {
      interactionId: 'ix.approve.k1',
      toolCallId: 'call-1',
      toolName: 'pipeshub@schedules/create',
      args: JSON.stringify(CRON),
      argsSummary: null,
      status: 'pending',
      errorCode: null,
    };
    render(<Theme><ApprovalCard approval={held} onDecide={() => {}} /></Theme>);
    expect(screen.getByTestId('schedule-summary').textContent).toBe(classicSummary);
    expect(words.calls).toEqual(['cron', 'cron']);
  });
});
