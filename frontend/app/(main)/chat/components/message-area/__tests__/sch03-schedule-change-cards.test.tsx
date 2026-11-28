/**
 * ADR-SCH-03 D5: `schedule_update` and `schedule_delete` ask every time, on the harness's card and on classic chat's,
 * and the card says the change from the schedule's current values: "Change Joke in two minutes: What it asks: a
 * short joke → a short animal joke", "Move …: old when → new when", "Pause …", "Delete …". Without current values
 * that match the call the card lists the arguments as any call's, never a guessed "before".
 */
import React from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import type { ApprovalReason, PendingApproval, ToolApprovalDetails } from '../../../types';
import { VECTOR_EXAMPLES, VECTOR_MASK } from '../../../__tests__/token-vector';

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
const schedules = vi.hoisted(() => ({ get: vi.fn(() => Promise.reject(new Error('unread'))) }));
vi.mock('@/app/(main)/workspace/schedules/api', () => ({ SchedulesApi: schedules }));
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

import { ApprovalCard } from '../approval-card';
import { ToolApprovalCard, ToolApprovalReceipts } from '../tool-approval-card';
import { settledToolApprovals } from '../../../tool-approval';
import { interactionAction, interactionTitle } from '../../../interaction-wording';

const plain = (text: string | null | undefined) => (text ?? '').replace(/[  ]/g, ' ');

const JOKE = {
  id: 'sch-1',
  name: 'Joke in two minutes',
  ask: 'Tell me a short joke.',
  when: { kind: 'cron', cron: '0 9 * * 1-5', timeZone: 'America/Los_Angeles' },
  notify: ['in_app'],
  status: 'active',
  revision: 3,
};

function held(toolName: string, args: Record<string, unknown>, schedule?: unknown, reason?: ApprovalReason): PendingApproval {
  return {
    interactionId: 'ix.approve.k1',
    toolCallId: 'call-1',
    toolName,
    args: JSON.stringify(args),
    argsSummary: null,
    status: 'pending',
    errorCode: null,
    ...(schedule !== undefined ? { schedule } : {}),
    ...(reason ? { reason } : {}),
  };
}

function classic(toolName: string, args: Record<string, unknown>, schedule?: Record<string, unknown>, over: Partial<ToolApprovalDetails> = {}): ToolApprovalDetails {
  return {
    approvalId: 'ap-1',
    instanceId: '',
    serverName: 'PipesHub',
    toolName,
    toolTitle: null,
    source: 'platform',
    kind: toolName === 'schedule_delete' ? 'destructive' : 'write',
    canRemember: true,
    expiresAt: Date.now() + 15 * 60_000,
    arguments: args,
    ...(schedule ? { schedule } : {}),
    ...over,
  };
}

const renderHarness = (approval: PendingApproval, props: Partial<React.ComponentProps<typeof ApprovalCard>> = {}) =>
  render(<Theme><ApprovalCard approval={approval} onDecide={() => {}} {...props} /></Theme>);
const renderClassic = (approval: ToolApprovalDetails, isLatest = true) =>
  render(<Theme><ToolApprovalCard approval={approval} isLatest={isLatest} rowId="row-1" /></Theme>);
const heading = () => screen.getByTestId('interaction-heading').textContent;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
beforeEach(() => {
  chat.state.slots['slot-1'].isOwner = true;
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('the harness card for schedule_update', () => {
  it('a new ask: "Change <name>" and "What it asks: <old> → <new>"', () => {
    renderHarness(held('pipeshub@schedules/update', { id: 'sch-1', revision: 3, ask: 'Tell me a short animal joke.' }, JOKE));
    expect(heading()).toBe('Change Joke in two minutes');
    expect(screen.getByTestId('schedule-change-ask').textContent).toBe('What it asksTell me a short joke. → Tell me a short animal joke.');
    expect(screen.getByTestId('schedule-change-note').textContent).toBe("Changes apply from its next run; a run already going isn't changed.");
    expect(screen.queryByTestId('approval-preview')).toBeNull();
  });

  it('a new time only: "Move <name>: <old when> → <new when>"', () => {
    renderHarness(
      held('schedule_update', { id: 'sch-1', revision: 3, when: { kind: 'cron', cron: '30 8 * * 1-5', timeZone: 'America/Los_Angeles' } }, JOKE),
    );
    expect(heading()).toBe('Move Joke in two minutes');
    expect(plain(screen.getByTestId('schedule-change-when').textContent)).toBe(
      'WhenEvery weekday at 9:00 AM (America/Los_Angeles) → Every weekday at 8:30 AM (America/Los_Angeles)',
    );
  });

  it('a rename and new notices list each change; unchanged fields are not shown', () => {
    renderHarness(
      held('schedule_update', { id: 'sch-1', revision: 3, name: 'Animal joke', notify: ['in_app', 'email'], ask: JOKE.ask }, JOKE),
    );
    expect(heading()).toBe('Change Joke in two minutes');
    expect(screen.getByTestId('schedule-change-name').textContent).toBe('NameJoke in two minutes → Animal joke');
    expect(screen.getByTestId('schedule-change-notify').textContent).toBe('Tell you onIn PipesHub → In PipesHub, Email');
    expect(screen.queryByTestId('schedule-change-ask')).toBeNull();
  });

  it.each([
    ['pause', 'Pause Joke in two minutes', "It won't run until you resume it."],
    ['resume', 'Resume Joke in two minutes', 'It runs again from its next time; the times it missed while paused are skipped.'],
    ['skip_next', 'Skip the next run of Joke in two minutes', 'Only its next run is skipped; after that it runs as usual.'],
    ['run_now', 'Run Joke in two minutes now', 'It runs once now; its schedule stays the same.'],
  ])('action %s: "%s"', (action, title, note) => {
    renderHarness(held('schedule_update', { id: 'sch-1', revision: 3, action }, JOKE));
    expect(heading()).toBe(title);
    expect(screen.getByTestId('schedule-change-note').textContent).toBe(note);
  });

  it('is never offered for the conversation or as a rule, whatever the reason says', () => {
    const reason = { rule: 'flow_external_write', sensitivity: 'private', sources: [], destination: 'x', conversationGrant: true, ruleGrant: true } as ApprovalReason;
    // The same reason on another tool offers both.
    renderHarness(held('slack__send_message', { channel: 'x', text: 'hi' }, undefined, reason), { conversationChoice: true, ruleChoice: true });
    expect(screen.getByRole('checkbox')).toBeTruthy();
    expect(screen.queryAllByText(/for this conversation/i).length).toBeGreaterThan(0);
    cleanup();
    renderHarness(held('schedule_update', { id: 'sch-1', revision: 3, action: 'pause' }, JOKE, reason), { conversationChoice: true, ruleChoice: true });
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.queryByText(/for this conversation/i)).toBeNull();
  });
});

describe('the harness card for schedule_delete', () => {
  it('"Delete <name>", with when it runs and what it asks', () => {
    renderHarness(held('pipeshub@schedules/delete', { id: 'sch-1', revision: 3 }, JOKE));
    expect(heading()).toBe('Delete Joke in two minutes');
    const change = screen.getByTestId('schedule-change');
    expect(plain(change.textContent)).toContain('Every weekday at 9:00 AM (America/Los_Angeles)');
    expect(change.textContent).toContain('Tell me a short joke.');
    expect(screen.getByTestId('schedule-change-note').textContent).toContain('It stops running');
  });
});

describe('never a wrong before and after: the arguments as any call’s', () => {
  it.each([
    ['no current values', { id: 'sch-1', revision: 3, ask: 'New' }, undefined],
    ['another schedule’s values', { id: 'sch-2', ask: 'New' }, JOKE],
    ['current values without the field', { id: 'sch-1', revision: 3, ask: 'New' }, { ...JOKE, ask: undefined }],
    ['current values with an unreadable time', { id: 'sch-1', revision: 3, when: { kind: 'cron', cron: '0 8 * * *', timeZone: 'UTC' } }, { ...JOKE, when: { kind: 'webhook' } }],
    ['an action with field changes', { id: 'sch-1', revision: 3, action: 'pause', ask: 'New' }, JOKE],
    ['an unknown action', { id: 'sch-1', revision: 3, action: 'explode' }, JOKE],
    ['no id', { ask: 'New' }, JOKE],
    ['current values of another revision', { id: 'sch-1', revision: 2, ask: 'New' }, JOKE],
    ['a call naming no revision', { id: 'sch-1', ask: 'New' }, JOKE],
    ['nothing that changes', { id: 'sch-1', revision: 3, ask: JOKE.ask }, JOKE],
  ])('%s', (_name, args, current) => {
    renderHarness(held('schedule_update', args, current));
    expect(heading()).toBe('Change a schedule');
    expect(screen.queryByTestId('schedule-change')).toBeNull();
    expect(screen.getByTestId('approval-preview')).toBeTruthy();
  });

  it('a delete without current values is "Delete a schedule" with its arguments', () => {
    renderHarness(held('schedule_delete', { id: 'sch-1', revision: 3 }));
    expect(heading()).toBe('Delete a schedule');
    expect(screen.getByTestId('approval-preview')).toBeTruthy();
  });

  it('the job screens and the buttons word them without the details', () => {
    const t = i18n.t.bind(i18n);
    expect(interactionTitle(t as never, { toolName: 'pipeshub@schedules/update' })).toBe('Change a schedule');
    expect(interactionAction(t as never, { toolName: 'schedule_delete' })).toBe('Delete a schedule');
    expect(interactionTitle(t as never, { toolName: 'mcp_evil_schedule_delete' })).not.toBe('Delete a schedule');
  });
});

describe('a harness card reads the schedule itself: its journaled ask has no place for the current values', () => {
  const asRead = {
    id: 'sch-1', name: JOKE.name, query: JOKE.ask, status: 'active', revision: 3, notify: ['in_app'],
    trigger: { kind: 'cron', cron: '0 9 * * 1-5', timeZone: 'America/Los_Angeles', catchUp: 'skip' },
  };

  it('an edit sent without current values says the change once the schedule is read, as the person', async () => {
    schedules.get.mockResolvedValueOnce(asRead as never);
    renderHarness(held('pipeshub@schedules/update', { id: 'sch-1', revision: 3, revision: 3, ask: 'Tell me a short animal joke.' }));
    await waitFor(() => expect(heading()).toBe('Change Joke in two minutes'));
    expect(schedules.get).toHaveBeenCalledWith('sch-1');
    expect(screen.getByTestId('schedule-change-ask').textContent).toBe('What it asksTell me a short joke. → Tell me a short animal joke.');
  });

  it('a delete and a pause read it too', async () => {
    schedules.get.mockResolvedValueOnce(asRead as never).mockResolvedValueOnce(asRead as never);
    renderHarness(held('pipeshub@schedules/delete', { id: 'sch-1', revision: 3, revision: 3 }));
    await waitFor(() => expect(heading()).toBe('Delete Joke in two minutes'));
    cleanup();
    renderHarness(held('pipeshub@schedules/update', { id: 'sch-1', revision: 3, revision: 3, action: 'pause' }));
    await waitFor(() => expect(heading()).toBe('Pause Joke in two minutes'));
  });

  it('values sent with the card are used as they are, never read again', () => {
    renderHarness(held('pipeshub@schedules/update', { id: 'sch-1', revision: 3, ask: 'Tell me a short animal joke.' }, JOKE));
    expect(heading()).toBe('Change Joke in two minutes');
    expect(schedules.get).not.toHaveBeenCalled();
  });

  it('a schedule that cannot be read leaves the arguments listed', async () => {
    renderHarness(held('pipeshub@schedules/update', { id: 'sch-1', revision: 3, ask: 'New' }));
    await waitFor(() => expect(schedules.get).toHaveBeenCalled());
    expect(heading()).toBe('Change a schedule');
    expect(screen.getByTestId('approval-preview')).toBeTruthy();
  });
});

describe('secrets are masked on both sides', () => {
  it.each(VECTOR_EXAMPLES.map((e) => [e.id, e.secret] as const))('%s', (_id, secret) => {
    const { container } = renderHarness(
      held('schedule_update', { id: 'sch-1', revision: 3, ask: `Post with ${secret}` }, { ...JOKE, name: `Digest ${secret}`, ask: `Use ${secret}` }),
    );
    expect(container.textContent).not.toContain(secret);
    expect(screen.getByTestId('schedule-change-ask').textContent).toContain(VECTOR_MASK);
  });
});

describe('classic chat’s card for schedule_update and schedule_delete', () => {
  it('says the change in the same words as the harness card, with Allow and Deny only', () => {
    const args = { id: 'sch-1', revision: 3, ask: 'Tell me a short animal joke.' };
    renderHarness(held('schedule_update', args, JOKE));
    const harness = screen.getByTestId('schedule-change').textContent;
    cleanup();
    renderClassic(classic('schedule_update', args, JOKE));
    expect(screen.getByText('Change Joke in two minutes')).toBeTruthy();
    expect(screen.getByTestId('schedule-change').textContent).toBe(harness);
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Allow', 'Deny']);
    expect(screen.queryByText('PipesHub')).toBeNull();
  });

  it('a pause and a delete', () => {
    renderClassic(classic('schedule_update', { id: 'sch-1', revision: 3, action: 'pause' }, JOKE));
    expect(screen.getByText('Pause Joke in two minutes')).toBeTruthy();
    cleanup();
    renderClassic(classic('schedule_delete', { id: 'sch-1', revision: 3 }, JOKE));
    expect(screen.getByText('Delete Joke in two minutes')).toBeTruthy();
    expect(screen.getByText("Can't be undone")).toBeTruthy();
  });

  it('without matching details: its title in general words and the arguments listed', () => {
    renderClassic(classic('schedule_update', { id: 'sch-1', revision: 3, ask: 'New ask' }));
    expect(screen.getByText('Change a schedule')).toBeTruthy();
    expect(screen.queryByTestId('schedule-change')).toBeNull();
    expect(screen.getByText('New ask')).toBeTruthy();
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('an MCP tool called schedule_update keeps the ordinary card', () => {
    renderClassic(classic('schedule_update', { id: 'sch-1', revision: 3, action: 'pause' }, JOKE, { source: 'mcp', serverName: 'Evil MCP' }));
    expect(screen.queryByTestId('schedule-change')).toBeNull();
    expect(screen.queryByText('Pause Joke in two minutes')).toBeNull();
    expect(screen.getByText('Evil MCP')).toBeTruthy();
  });

  it('its notices and receipts say what happened to the schedule', () => {
    renderClassic(classic('schedule_delete', { id: 'sch-1', revision: 3 }, JOKE), false);
    expect(screen.getByTestId('tool-approval-card').textContent).toBe("Deleting the schedule wasn't approved, so it was kept.");
    cleanup();
    const parts = [
      { type: 'tool_call', toolCallId: 'a', status: 'completed', approvalAnswer: 'allowed', approval: classic('schedule_update', { id: 'sch-1', revision: 3, action: 'pause' }, JOKE) },
      { type: 'tool_call', toolCallId: 'b', status: 'blocked', approvalAnswer: 'declined', approval: classic('schedule_delete', { id: 'sch-1', revision: 3 }, JOKE) },
    ];
    render(<Theme><ToolApprovalReceipts items={settledToolApprovals(parts as never)} /></Theme>);
    expect(screen.getByTestId('tool-approval-receipts').textContent).toBe(
      'You allowed the change to the scheduleYou declined deleting the schedule',
    );
  });
});
