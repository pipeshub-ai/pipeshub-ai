/**
 * CTL-4: a coder (engine) run shows its work on the answer's timeline, as a loop run does: commands and tool calls
 * as steps, the files it changed with their diffs inline, the full detail behind "View activity", and no separate
 * code-task card. Loop runs that make files through run_code show the same "Files changed" block. The acceptance
 * case is the saved answer of run 9d1315d0.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import testI18n from '@/lib/__tests__/test-i18n';
import type { MessagePart, SSEEngineProgressEvent } from '@/chat/types';
import SAVED from '../../../__tests__/fixtures/ctl4-9d1315d0.json';

// MessageActions forks through the app router (main #4225); these tests render no router.
vi.mock('next/navigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('next/navigation')>()),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
}));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => <div role="status">Loading</div> }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));
vi.mock('@/lib/api', async () => ({
  ...(await vi.importActual<object>('@/lib/api')),
  apiClient: { get: vi.fn(() => new Promise(() => {})), post: vi.fn(() => new Promise(() => {})) },
}));
const streamRecord = vi.hoisted(() => vi.fn(() => new Promise(() => {})));
vi.mock('@/app/(main)/knowledge-base/api', () => ({ KnowledgeBaseApi: { streamRecord, getRecordDetails: vi.fn() } }));

import { ChatResponse } from '../chat-response';
import { buildActivitySummary } from '../agent-activity';
import { changedFilesFromEngine, engineSteps } from '../../../engine-steps';
import { createAGUIEventHandler } from '../../../agui-event-handler';
import { useChatStore } from '@/chat/store';

const initialChat = useChatStore.getState();
const RUN_ID = SAVED.runId;
const PARTS = SAVED.parts as MessagePart[];
const FRAMES = (PARTS[0]!.frames ?? []) as SSEEngineProgressEvent[];

beforeEach(() => {
  useChatStore.setState(initialChat, true);
  const store = useChatStore.getState();
  const slotId = store.createSlot('conv-1');
  store.updateSlot(slotId, { isInitialized: true, isOwner: true });
  store.setActiveSlot(slotId);
  streamRecord.mockClear();
});
afterEach(() => cleanup());

describe('CTL-4: the engine run\'s steps and files', () => {
  it('reads the saved frames as steps, in order', () => {
    const steps = engineSteps(FRAMES, true).filter((step) => step.kind !== 'file');
    expect(steps.map((step) => [step.kind, step.kind === 'command' ? step.command : step.kind === 'tool' ? step.toolName : '', step.status])).toEqual([
      ['command', '/bin/bash -lc pwd', 'completed'],
      ['tool', 'knowledgegraph__search_entities', 'completed'],
      ['tool', 'knowledgegraph__fetch_record', 'completed'],
      ['command', expect.stringContaining('cat > out/travel_policy.md'), 'completed'],
    ]);
  });

  it('a failed command says its exit code; one still running says so while the run works', () => {
    const frames: SSEEngineProgressEvent[] = [
      { tool: 'code_task', taskId: 't', kind: 'command', text: 'pytest -q', data: { callId: 'c1' } },
      { tool: 'code_task', taskId: 't', kind: 'output', text: '1 failed\n', data: { callId: 'c1' } },
      { tool: 'code_task', taskId: 't', kind: 'command_done', text: '', data: { callId: 'c1', exitCode: 1 } },
      { tool: 'code_task', taskId: 't', kind: 'command', text: 'npm test', data: { callId: 'c2' } },
    ];
    const [failed, running] = engineSteps(frames, false);
    expect(failed).toMatchObject({ kind: 'command', status: 'failed', exitCode: 1, output: '1 failed\n' });
    expect(running).toMatchObject({ kind: 'command', status: 'running' });
  });

  it('lists the files it changed with what changed', () => {
    expect(changedFilesFromEngine(FRAMES)).toEqual([
      expect.objectContaining({ path: 'out/travel_policy.md', change: 'added', additions: 6, deletions: 0, artifactId: 'f07a0e28-9d74-4082-97ca-0bc8dc7866a4' }),
    ]);
  });

  it('names what the run did in one line', () => {
    expect(buildActivitySummary(PARTS, testI18n.t, null, undefined, changedFilesFromEngine(FRAMES))).toBe(
      'Ran 2 commands · Used Knowledge Graph: Search entities · Used Knowledge Graph: Fetch record · Created travel_policy.md',
    );
    const named = FRAMES.map((frame) =>
      frame.kind === 'tool' && frame.text === 'knowledgegraph__fetch_record'
        ? { ...frame, data: { ...frame.data, displayName: 'Read the full record' } }
        : frame,
    );
    expect(buildActivitySummary([{ ...PARTS[0]!, frames: named }], testI18n.t)).toBe(
      'Ran 2 commands · Used Knowledge Graph: Search entities · Read the full record',
    );
  });

  it('the live engine frames build the same part the answer saves', () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    FRAMES.slice(0, 3).forEach((frame, i) =>
      handle({ id: String(i), event: 'CUSTOM', data: { type: 'CUSTOM', name: 'engine_progress', value: frame, runId: RUN_ID } }),
    );
    const engine = parts.find((part) => part.type === 'engine_task');
    expect(engine?.frames).toEqual(FRAMES.slice(0, 3));
  });
});

describe('CTL-4: the saved answer of run 9d1315d0', () => {
  const renderAnswer = () =>
    render(
      <Theme>
        <ChatResponse question="what is our travel policy?" answer={SAVED.content} messageId="m1" persistedParts={PARTS} runId={RUN_ID} />
      </Theme>,
    );

  it('shows its steps and files on the timeline, and no code-task card', () => {
    renderAnswer();
    expect(screen.queryByRole('region', { name: 'Code task' })).toBeNull();
    const header = screen.getByTestId('activity-summary');
    expect(header.textContent).toContain('Ran 2 commands');
    expect(header.textContent).toContain('Created travel_policy.md');
    fireEvent.click(header);
    expect(screen.getByText('/bin/bash -lc pwd')).toBeTruthy();
    const files = screen.getByRole('list', { name: 'Files changed' });
    const row = within(files).getByRole('button', { name: /out\/travel_policy\.md/ });
    expect(row.textContent).toContain('added');
    expect(row.textContent).toContain('+6');
    fireEvent.click(row);
    expect(screen.getByText('+# Travel policy')).toBeTruthy();
  });
});

describe('CTL-4: a loop run that made a file through run_code', () => {
  it('shows the same block; a new file without a diff opens its preview', async () => {
    const answer =
      'The report is ready.\n\n::artifact[report.pdf](record:rec-9){application/pdf|doc-9|rec-9|DOCUMENT|1}';
    const parts: MessagePart[] = [
      { type: 'tool_call', toolCallId: 'c1', toolName: 'coding_sandbox__run_code', displayName: 'Ran code', status: 'completed', runId: 'run-l' },
      { type: 'text', content: 'The report is ready.', isFinal: true, runId: 'run-l' },
    ];
    render(
      <Theme>
        <ChatResponse question="make the report" answer={answer} messageId="m2" persistedParts={parts} runId="run-l" />
      </Theme>,
    );
    fireEvent.click(screen.getByTestId('activity-summary'));
    const files = screen.getByRole('list', { name: 'Files changed' });
    const row = within(files).getByRole('button', { name: /report\.pdf/ });
    expect(row.textContent).toContain('added');
    fireEvent.click(row);
    await waitFor(() => expect(streamRecord).toHaveBeenCalledWith('rec-9', expect.objectContaining({ version: 1 })));
  });
});
