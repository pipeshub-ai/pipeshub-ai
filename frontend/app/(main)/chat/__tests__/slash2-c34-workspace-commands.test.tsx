/**
 * SLASH2 C3/C4: /diff, /undo, /review and /init in the browser. Each one greys out with its reason where it does
 * not apply (O8) and waits while a reply runs (O7, /diff acts at once); /diff's row lists the files with their
 * diffs and offers /undo and /review; /undo shows the files first, restores only after a confirm, through the
 * Workspace versions button's own call, and never touches the conversation (O3); /init's answer opens the
 * draft in the agent editor for its editors, unsaved.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import i18n from 'i18next';
import { FILL_COMPOSER_COMMAND, useCommandStore } from '@/lib/store/command-store';

const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => router }));

const api = vi.hoisted(() => ({
  getWorkspaceDiff: vi.fn(),
  restoreWorkspaceVersion: vi.fn(),
  listWorkspaceVersions: vi.fn(),
}));
vi.mock('../api', () => ({
  ChatApi: {
    getWorkspaceDiff: (...a: unknown[]) => api.getWorkspaceDiff(...a),
    restoreWorkspaceVersion: (...a: unknown[]) => api.restoreWorkspaceVersion(...a),
    listWorkspaceVersions: (...a: unknown[]) => api.listWorkspaceVersions(...a),
  },
}));

import { builtinReason, findBuiltin, isPromptBuiltin, takesArgsFirst, type BuiltinCommand, type BuiltinContext } from '../builtin-commands';
import { confirmUndo, runBuiltin, savedUndoRowId, startUndo, type BuiltinDeps } from '../builtin-actions';
import { useBuiltinResults, useWorkspaceHeads, type BuiltinRow } from '../builtin-results';
import { builtinReasonText } from '../components/chat-panel/slash-command-menu';
import { BuiltinResultRows, useSavedUndoRows } from '../components/message-area/builtin-result-rows';
import { InitDraftAction } from '../components/message-area/init-draft-action';
import { useChatStore } from '../store';
import { instructionsFromAnswer, useInstructionsDraft } from '@/app/(main)/agents/agent-builder/instructions-draft';
import type { WorkspaceDiff } from '../types';
import type { MessagePair } from '../components/message-area/message-pairs';

const t = i18n.t.bind(i18n) as unknown as BuiltinDeps['t'];
const SESSION = { agentId: 'agent-1', sessionId: 'conv-1' };

const ALL: BuiltinContext = {
  running: false,
  conversation: true,
  answer: true,
  harness: true,
  forkable: true,
  reasoningModel: true,
  planStrategy: true,
  mcp: true,
  knowledge: true,
  memory: true,
  skills: true,
  workspace: true,
  agentChat: true,
  agentEditor: true,
};

const get = (name: string): BuiltinCommand => {
  const c = findBuiltin(name);
  if (!c) throw new Error(name);
  return c;
};

const DIFF: WorkspaceDiff = {
  workspaceId: 'ws:conv-1',
  head: 4,
  from: 2,
  to: 4,
  files: [
    { path: 'notes/draft.md', change: 'deleted', sizeBytes: 9, additions: 0, deletions: 8, diff: '--- a/notes/draft.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n' },
    { path: 'out/summary.csv', change: 'added', sizeBytes: 30, additions: 17, deletions: 0, diff: '--- /dev/null\n+++ b/out/summary.csv\n@@ -0,0 +1 @@\n+trip,cost\n' },
    { path: 'out/travel_policy.md', change: 'modified', sizeBytes: 900, additions: 31, deletions: 4, diff: '--- a/out/travel_policy.md\n+++ b/out/travel_policy.md\n@@ -1 +1 @@\n-old\n+new\n' },
  ],
  additions: 48,
  deletions: 12,
  omitted: 0,
};

function deps(over: Partial<BuiltinDeps> = {}): BuiltinDeps {
  return {
    t,
    rowKey: 'slot-1',
    afterPairs: 2,
    slot: { convId: 'conv-1', threadAgentId: 'agent-1', messages: [] } as unknown as BuiltinDeps['slot'],
    agentId: 'agent-1',
    running: false,
    harness: true,
    isAdmin: false,
    models: [],
    currentModel: null,
    selectModel: vi.fn(),
    setEffort: vi.fn(),
    setStrategy: vi.fn(),
    modeLabel: null,
    openModelPanel: vi.fn(),
    openKnowledgePanel: vi.fn(),
    knowledgeSelection: [],
    stop: vi.fn(),
    navigate: vi.fn(),
    ...over,
  };
}

const rows = (key = 'slot-1'): BuiltinRow[] => useBuiltinResults.getState().rows[key] ?? [];

function Rows({ rowKey = 'slot-1' }: { rowKey?: string }) {
  const list = useBuiltinResults((s) => s.rows[rowKey] ?? []);
  return <BuiltinResultRows rows={list} rowKey={rowKey} afterPairs={2} last />;
}

const initialChat = useChatStore.getState();

beforeEach(() => {
  useBuiltinResults.setState({ rows: {}, pairs: {} });
  useWorkspaceHeads.setState({ heads: {} });
  useCommandStore.setState({ handlers: {}, pending: {} });
  useChatStore.setState(initialChat, true);
  useInstructionsDraft.setState({ draft: null });
  for (const fn of Object.values(api)) fn.mockReset();
  router.push.mockReset();
});

afterEach(() => cleanup());

describe('greyed out with a reason (O8) and waiting while a reply runs (O7)', () => {
  it('/diff, /undo and /review: "This chat has no workspace yet" without one', () => {
    for (const name of ['diff', 'undo', 'review']) {
      const reason = builtinReason(get(name), { ...ALL, workspace: false });
      expect(reason, name).toBe('noWorkspace');
      expect(builtinReasonText(t, get(name), reason!)).toBe('This chat has no workspace yet');
      expect(builtinReason(get(name), ALL), name).toBeNull();
    }
  });

  it('while a reply runs /diff acts at once; /undo, /review and /init wait for it', () => {
    const running = { ...ALL, running: true };
    expect(builtinReason(get('diff'), running)).toBeNull();
    for (const name of ['undo', 'review', 'init']) expect(builtinReason(get(name), running), name).toBe('wait');
  });

  it('/init: only in an agent chat, only for its editors, once the chat is saved', () => {
    const init = get('init');
    expect(builtinReason(init, { ...ALL, agentChat: false })).toBe('noAgent');
    expect(builtinReasonText(t, init, 'noAgent')).toBe('Use /init in a chat with an agent');
    expect(builtinReason(init, { ...ALL, agentEditor: false })).toBe('notEditor');
    expect(builtinReasonText(t, init, 'notEditor')).toBe("Only the agent's editors can change its instructions");
    expect(builtinReason(init, { ...ALL, conversation: false })).toBe('noConversation');
    expect(builtinReason(init, ALL)).toBeNull();
  });

  it('/review and /init are prompt commands: picked from the menu they wait for Enter, and are sent, never run here', () => {
    for (const name of ['review', 'init']) {
      expect(isPromptBuiltin(get(name)), name).toBe(true);
      expect(takesArgsFirst(get(name)), name).toBe(true);
    }
    expect(isPromptBuiltin(get('diff'))).toBe(false);
    expect(takesArgsFirst(get('diff'))).toBe(false);
  });
});

describe('/diff', () => {
  it('reads the whole chat\'s changes and shows them as a local row: counts, each file with + and −, its diff on click', async () => {
    api.getWorkspaceDiff.mockResolvedValue(DIFF);
    await runBuiltin(get('diff'), '', deps());
    expect(api.getWorkspaceDiff).toHaveBeenCalledWith(SESSION, 'chat');
    expect(useWorkspaceHeads.getState().heads['conv-1']?.head).toBe(4);
    render(<Theme><Rows /></Theme>);
    expect(screen.getByText('Files changed since this chat began: 3')).toBeTruthy();
    expect(screen.getByText('+48')).toBeTruthy();
    expect(screen.getByText('−12')).toBeTruthy();
    expect(screen.getByText('out/travel_policy.md')).toBeTruthy();
    expect(screen.getByText('+31')).toBeTruthy();
    expect(screen.queryByText('+new')).toBeNull();
    fireEvent.click(screen.getByText('out/travel_policy.md'));
    expect(screen.getByText('+new')).toBeTruthy();
  });

  it('a chat with no workspace says so instead of a list', async () => {
    api.getWorkspaceDiff.mockResolvedValue({ ...DIFF, head: 0, from: 0, to: 0, files: [], additions: 0, deletions: 0 });
    await runBuiltin(get('diff'), '', deps());
    expect(rows()[0]?.result).toEqual({ kind: 'note', text: 'This chat has no workspace yet' });
  });

  it('"Review these changes" puts /review in the composer; "Undo last turn" is /undo; both wait while a reply runs', async () => {
    api.getWorkspaceDiff.mockResolvedValue(DIFF);
    const fill = vi.fn();
    useCommandStore.getState().register(FILL_COMPOSER_COMMAND, fill);
    await runBuiltin(get('diff'), '', deps());
    useChatStore.setState({ slots: { 'slot-1': { isStreaming: true } as never } });
    render(<Theme><Rows /></Theme>);
    const review = screen.getByRole('button', { name: 'Review these changes' }) as HTMLButtonElement;
    const undo = screen.getByRole('button', { name: 'Undo last turn' }) as HTMLButtonElement;
    expect(review.disabled && undo.disabled).toBe(true);
    expect(screen.getByText('Wait for this run to finish')).toBeTruthy();
    act(() => useChatStore.setState({ slots: { 'slot-1': { isStreaming: false } as never } }));
    fireEvent.click(review);
    expect(fill).toHaveBeenCalledWith({ text: '/review ' });
    fireEvent.click(undo);
    await waitFor(() => expect(api.getWorkspaceDiff).toHaveBeenLastCalledWith(SESSION, 'turn'));
  });
});

describe('/undo (files only, O3)', () => {
  it('shows the last turn\'s files and restores nothing until confirmed; then the Workspace versions restore, at the head shown', async () => {
    api.getWorkspaceDiff.mockResolvedValue(DIFF);
    api.restoreWorkspaceVersion.mockResolvedValue({ revision: 5 });
    useChatStore.setState({ slots: { 'slot-1': { messages: [{ id: 'm1' }], isStreaming: false } as never } });
    const before = useChatStore.getState().slots['slot-1'];
    await runBuiltin(get('undo'), '', deps());
    expect(api.getWorkspaceDiff).toHaveBeenCalledWith(SESSION, 'turn');
    expect(api.restoreWorkspaceVersion).not.toHaveBeenCalled();
    render(<Theme><Rows /></Theme>);
    expect(screen.getByText('Restore these files to how they were before the last reply? Files: 3')).toBeTruthy();
    expect(screen.getByText('Only files go back. Your messages stay; to rewind the conversation, fork from a message.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Restore files' }));
    await waitFor(() => expect(api.restoreWorkspaceVersion).toHaveBeenCalledWith(SESSION, 2, 4));
    await waitFor(() => expect(screen.getByText('Files restored to version 2 (3 changed back). Now version 5.')).toBeTruthy());
    expect(rows().map((r) => r.id)).toEqual([savedUndoRowId(5)]);
    expect(useChatStore.getState().slots['slot-1']).toBe(before);
    expect(useWorkspaceHeads.getState().heads['conv-1']?.head).toBe(5);
  });

  it('Cancel drops the row; nothing is restored', async () => {
    api.getWorkspaceDiff.mockResolvedValue(DIFF);
    await runBuiltin(get('undo'), '', deps());
    render(<Theme><Rows /></Theme>);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(rows()).toEqual([]);
    expect(api.restoreWorkspaceVersion).not.toHaveBeenCalled();
  });

  it('a writer at work refuses it with the restore button\'s own message (O7); a moved head says run it again', async () => {
    api.getWorkspaceDiff.mockResolvedValue(DIFF);
    for (const [code, text] of [
      ['HTTP_AGENT_HARNESS_WORKSPACE_WRITER_BUSY', i18n.t('durableRun.versions.busy')],
      ['HTTP_AGENT_HARNESS_WORKSPACE_CONFLICT', 'The workspace changed since this was shown. Run /undo again.'],
    ] as const) {
      useBuiltinResults.setState({ rows: {} });
      api.restoreWorkspaceVersion.mockResolvedValue({ refused: code });
      await startUndo(SESSION, { rowKey: 'slot-1', afterPairs: 2, typed: '/undo' }, t);
      await confirmUndo('slot-1', rows()[0]!.id);
      const result = rows()[0]?.result;
      expect(result?.kind).toBe('undoConfirm');
      render(<Theme><Rows /></Theme>);
      expect(screen.getByText(text)).toBeTruthy();
      cleanup();
    }
  });

  it('nothing written since: "No workspace changes to undo"', async () => {
    api.getWorkspaceDiff.mockResolvedValue({ ...DIFF, from: 4, files: [], additions: 0, deletions: 0 });
    await runBuiltin(get('undo'), '', deps());
    expect(rows()[0]?.result).toEqual({ kind: 'note', text: 'No workspace changes to undo' });
  });

  it('a restore is saved (O6): on reload its row comes back after the reply it followed', async () => {
    api.listWorkspaceVersions.mockResolvedValue({
      workspaceId: 'ws:conv-1',
      head: 5,
      versions: [
        { revision: 5, parentRevision: 4, reason: 'rollback', createdAt: Date.parse('2026-10-19T10:05:00Z'), fileCount: 2, contentHash: 'h', restoredByUser: true },
        { revision: 4, parentRevision: 3, reason: 'turn_end', createdAt: Date.parse('2026-10-19T10:03:00Z'), fileCount: 3, contentHash: 'h', restoredByUser: false },
      ],
    });
    useChatStore.setState({ slots: { 'slot-1': { convId: 'conv-1', threadAgentId: 'agent-1', isOwner: true, durableRun: { agentId: 'agent-1', sessionId: 'conv-1', runId: 'r' } } as never } });
    const pairs = [{ createdAt: '2026-10-19T10:00:00Z' }, { createdAt: '2026-10-19T10:02:00Z' }, { createdAt: '2026-10-19T10:06:00Z' }] as MessagePair[];
    function Hydrate() {
      useSavedUndoRows('slot-1', pairs);
      return null;
    }
    render(<Hydrate />);
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(api.listWorkspaceVersions).toHaveBeenCalledWith(SESSION, { limit: 50 });
    expect(rows()[0]).toMatchObject({ id: savedUndoRowId(5), afterPairs: 2, typed: 'Workspace restored', result: { kind: 'undone', revision: 5 } });
  });

  it('CJFIX-B (F3, D14): a shared chat’s viewer is not asked for the owner’s workspace versions', async () => {
    useChatStore.setState({ slots: { 'slot-1': { convId: 'conv-1', threadAgentId: 'agent-1', isOwner: false, durableRun: { agentId: 'agent-1', sessionId: 'conv-1', runId: 'r' } } as never } });
    const pairs = [{ createdAt: '2026-10-19T10:00:00Z' }] as MessagePair[];
    function Hydrate() {
      useSavedUndoRows('slot-1', pairs);
      return null;
    }
    render(<Hydrate />);
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(api.listWorkspaceVersions).not.toHaveBeenCalled();
    expect(rows()).toHaveLength(0);
  });

  it('QAFIX4: a saved /upload row keeps its file list on reload, and Undo while the upload is still the last change', async () => {
    api.listWorkspaceVersions.mockResolvedValue({
      workspaceId: 'ws:conv-1',
      head: 3,
      versions: [
        { revision: 3, parentRevision: 2, reason: 'upload', createdAt: Date.parse('2026-10-19T10:05:00Z'), fileCount: 3, contentHash: 'h', restoredByUser: false, paths: ['uploads/b.csv'] },
        { revision: 2, parentRevision: 1, reason: 'upload', createdAt: Date.parse('2026-10-19T10:03:00Z'), fileCount: 2, contentHash: 'h', restoredByUser: false, paths: ['uploads/a.csv', 'uploads/n.md'] },
      ],
    });
    useChatStore.setState({ slots: { 'slot-1': { convId: 'conv-1', threadAgentId: 'agent-1', isOwner: true, durableRun: { agentId: 'agent-1', sessionId: 'conv-1', runId: 'r' } } as never } });
    const pairs = [{ createdAt: '2026-10-19T10:00:00Z' }] as MessagePair[];
    function Hydrate() {
      useSavedUndoRows('slot-1', pairs);
      return null;
    }
    render(<Hydrate />);
    await waitFor(() => expect(rows()).toHaveLength(2));
    const byRevision = (revision: number) => rows().find((row) => row.result.kind === 'uploaded' && row.result.revision === revision);
    expect(byRevision(3)?.result).toEqual({ kind: 'uploaded', revision: 3, paths: ['uploads/b.csv'], session: SESSION });
    expect(byRevision(2)?.result).toEqual({ kind: 'uploaded', revision: 2, paths: ['uploads/a.csv', 'uploads/n.md'] });
    render(<Theme><BuiltinResultRows rows={rows()} rowKey="slot-1" afterPairs={1} /></Theme>);
    expect(await screen.findByText('Added to the workspace as version 3 · files: 1')).toBeTruthy();
    expect(screen.getByText('uploads/n.md')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Undo' })).toHaveLength(1);
  });
});

describe('/init (O4)', () => {
  it('the answer\'s button opens the draft in the agent editor, for its editors only; nothing is saved', () => {
    useChatStore.setState({
      activeSlotId: 'slot-1',
      slots: { 'slot-1': { threadAgentId: 'agent-1' } as never },
      agentContextAccess: { canEdit: true } as never,
    });
    const answer = 'Here is a draft:\n\n```text\nYou answer travel questions.\nCite the policy.\n```\n';
    render(<Theme><InitDraftAction answer={answer} /></Theme>);
    expect(screen.getByText('Nothing is saved until you save the agent.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Open in the agent editor/ }));
    expect(router.push).toHaveBeenCalledWith('/agents/edit?agentKey=agent-1');
    expect(useInstructionsDraft.getState().take('agent-2')).toBeNull();
    expect(useInstructionsDraft.getState().take('agent-1')).toBe('You answer travel questions.\nCite the policy.');
    expect(useInstructionsDraft.getState().take('agent-1')).toBeNull();
    cleanup();
    useChatStore.setState({ agentContextAccess: { canEdit: false } as never });
    render(<Theme><InitDraftAction answer={answer} /></Theme>);
    expect(screen.queryByRole('button', { name: /Open in the agent editor/ })).toBeNull();
  });

  it('an answer without a fenced block is taken whole', () => {
    expect(instructionsFromAnswer('  Be brief.  ')).toBe('Be brief.');
  });
});
