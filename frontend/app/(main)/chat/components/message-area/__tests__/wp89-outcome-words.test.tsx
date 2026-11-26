/**
 * WP89-F (ADR-RUN-01 D1, WP-89, C10.05, C2.32, C10.06): the chat words a run's settled outcome in the reader's
 * language with the action that fits; an answer saved with the presenter's English words renders as saved with no
 * second note; the steps' header shows the running cost beside the elapsed time; a long command's whole log is one
 * click away in the activity drawer; the drawer holds a read-only tree of the conversation's files; a state notice
 * journaled again shows again; a queued task says roughly when it tries again.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import '@/lib/__tests__/test-i18n';

const sendAgain = vi.fn(() => true);
vi.mock('../error-next-step', () => ({ sendAgain: (text: string) => sendAgain(text) }));
const RUN = { sessionId: 'ses-1', runId: 'run-1' };
vi.mock('../../activity-panel/use-activity-run', () => ({ useMessageRun: () => RUN }));
const listWorkspaceFiles = vi.fn();
vi.mock('../../../api', () => ({ ChatApi: { listWorkspaceFiles: (...args: unknown[]) => listWorkspaceFiles(...args) } }));

import { createAGUIEventHandler } from '../../../agui-event-handler';
import { sandboxWaitText } from '../../../harness-errors';
import { hasLegacyOutcomeWords, runOutcomeOf } from '../../../run-outcome';
import type { MessagePart } from '../../../types';
import { useActivityPanelStore } from '../../activity-panel/activity-panel-store';
import { WorkspaceTree } from '../../activity-panel/workspace-tree';
import { buildActivitySummary, EngineCommandRow, RunNotices } from '../agent-activity';
import { OutcomeNotice } from '../outcome-notice';

const LOCALES = ['en-US', 'en-IN', 'de-DE', 'es-ES', 'hi-IN', 'ko-KR', 'zh-CN', 'zh-SG', 'zh-TW', 'cs-CZ'];

function finalPart(runOutcome: Record<string, unknown>): MessagePart[] {
  return [{ type: 'text', content: '', isFinal: true, runOutcome }];
}

const UNWRITTEN = { kind: 'partial', reason: 'time', action: 'continue', missing: 'report.md', sources: 21, kept: true, opening: false, closing: 'English' };
const NOTHING = { kind: 'partial', reason: 'turns', action: 'retry', missing: 'report.md', sources: 0, kept: false, opening: false, closing: 'English' };
const ENGINE_FILE = { kind: 'partial', reason: 'time', action: 'continue', file: { name: 'report.md', by: 'engine' }, sources: 3, kept: true, opening: true, closing: 'English' };

const lines = () => screen.getByTestId('outcome-notice').textContent ?? '';

beforeEach(async () => {
  await i18n.changeLanguage('en-US');
  sendAgain.mockClear();
});

afterEach(() => cleanup());

describe('WP89-F: the run’s outcome, worded by the chat', () => {
  it('WP89-F1: a file the run did not write: why, what it gathered, that it was kept, and Continue', () => {
    const outcome = runOutcomeOf(undefined, finalPart(UNWRITTEN))!;
    render(<Theme><OutcomeNotice outcome={outcome} actionable /></Theme>);
    expect(lines()).toContain("report.md isn't written yet.");
    expect(lines()).toContain('Stopped early: time limit reached');
    expect(lines()).toContain('It read 21 sources.');
    expect(lines()).toContain("What it gathered is saved in this conversation's files.");
    expect(lines()).not.toContain('English');
    fireEvent.click(screen.getByTestId('outcome-action'));
    expect(sendAgain).toHaveBeenCalledWith('continue');
    expect(screen.getByTestId('outcome-action').hasAttribute('disabled')).toBe(true);
  });

  it('WP89-F2: nothing gathered offers Try again and never says anything was saved', () => {
    render(<Theme><OutcomeNotice outcome={runOutcomeOf(undefined, finalPart(NOTHING))!} actionable /></Theme>);
    expect(lines()).toContain('It stopped before it gathered anything.');
    expect(lines()).not.toContain('saved');
    expect(screen.getByTestId('outcome-action').dataset.action).toBe('retry');
    expect(screen.getByTestId('outcome-action').textContent).toContain('Try again');
  });

  it('WP89-F3: the engine’s file is the result; the answer is its opening and the note names the file', () => {
    render(<Theme><OutcomeNotice outcome={runOutcomeOf(undefined, finalPart(ENGINE_FILE))!} /></Theme>);
    expect(lines()).toContain('report.md holds what the task had written when it stopped');
    expect(lines()).toContain('The full report is in report.md.');
    expect(screen.queryByTestId('outcome-action')).toBeNull();
  });

  it.each(LOCALES)('WP89-F4: %s words every outcome line, translated', (locale) => {
    const keys = ['unwritten', 'endedWithout', 'nothingGathered', 'kept', 'lost', 'writtenByEngine', 'savedPartial',
      'writtenFromGathered', 'fullReport', 'partial', 'continue', 'retry'].map((k) => `chat.outcome.${k}`);
    for (const key of [...keys, 'agentActivity.summary.costSoFar', 'activityPanel.section.workspace', 'agentActivity.command.fullLogInActivity']) {
      expect(i18n.exists(key, { lng: locale, fallbackLng: false }), `${locale} ${key}`).toBe(true);
      if (!locale.startsWith('en') && key !== 'chat.outcome.retry')
        expect(i18n.t(key, { lng: locale, name: 'N', cost: 'C' }), `${locale} ${key}`).not.toBe(i18n.t(key, { lng: 'en-US', name: 'N', cost: 'C' }));
    }
    expect(i18n.t('chat.outcome.sources', { lng: locale, count: 3 })).toContain('3');
  });

  it('WP89-F4: a completed outcome shows no note', () => {
    render(<Theme><OutcomeNotice outcome={{ ...runOutcomeOf(undefined, finalPart(UNWRITTEN))!, kind: 'completed' }} /></Theme>);
    expect(screen.queryByTestId('outcome-notice')).toBeNull();
  });

  it('WP89-F5: an answer saved with the presenter’s English words is recognised, so no second note is added', () => {
    expect(hasLegacyOutcomeWords('Findings.\n\n---\n**This result is partial.** This stopped early when …')).toBe(true);
    expect(hasLegacyOutcomeWords("**report.md isn't written yet.** This task stopped because …")).toBe(true);
    expect(hasLegacyOutcomeWords('This reply stopped early because it reached its step limit, before …')).toBe(true);
    expect(hasLegacyOutcomeWords('# Report\n\nThe result is partial only for 2024.')).toBe(false);
    expect(runOutcomeOf(undefined, [{ type: 'text', content: 'x', isFinal: true }])).toBeNull();
  });
});

describe('WP89-F: the steps’ header, the command log, the drawer’s files, the notices', () => {
  it('WP89-F6 (C10.05): while the run works the header shows its time and what it has spent so far', () => {
    const summary = buildActivitySummary([{ type: 'tool_call', toolName: 'web_search', status: 'completed' }], i18n.t, null, 374_000, [], '$0.42');
    expect(summary).toMatch(/6m 14s · \$0\.42 so far$/);
    expect(buildActivitySummary([], i18n.t, null, 5_000, [], null)).not.toContain('so far');
  });

  it('WP89-F7 (C2.32): a long output shows its last lines, and its whole log opens in the activity drawer', () => {
    const output = Array.from({ length: 40_000 }, (_, n) => `line ${n} ${'x'.repeat(240)}`).join('\n');
    expect(output.length).toBeGreaterThan(10_000_000);
    render(<Theme><EngineCommandRow part={{ type: 'engine_command', command: 'pytest -q', status: 'completed', content: output, runId: 'run-1' }} /></Theme>);
    fireEvent.click(screen.getByText('pytest -q'));
    expect(screen.getByTestId('engine-command-output').textContent!.split('\n')).toHaveLength(20);
    fireEvent.click(screen.getByTestId('engine-command-full-log'));
    const state = useActivityPanelStore.getState();
    expect(state.run).toEqual(RUN);
    expect(state.focusSection).toBe('commands');
  });

  it('WP89-F8 (C10.06): the drawer’s workspace tree is folded, read only, and lists the files when opened', async () => {
    listWorkspaceFiles.mockResolvedValue({
      workspaceId: 'ws', revision: 7, createdAt: 0, totalBytes: 141_000, omitted: 0,
      files: [{ path: 'out/report.md', sizeBytes: 138_000 }, { path: 'notes/handoff.json', sizeBytes: 3_000 }],
      references: [{ path: 'repos/onyx', label: 'onyx-dot-app/onyx', address: 'https://github.com/onyx-dot-app/onyx', version: '57cb7b8' }],
    });
    render(<Theme><WorkspaceTree run={RUN} /></Theme>);
    expect(screen.queryByTestId('workspace-tree')).toBeNull();
    expect(listWorkspaceFiles).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Workspace files/ }));
    await waitFor(() => expect(screen.getByTestId('workspace-tree')).toBeTruthy());
    const tree = screen.getByTestId('workspace-tree').textContent ?? '';
    expect(tree).toContain('report.md');
    expect(tree).toContain('handoff.json');
    expect(screen.getByText(/Read only\./)).toBeTruthy();
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  it('WP89-F9: a retire after a resume shows again, live; the latest state stands', () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    const frame = (code: string, occurrence: number) => ({
      id: `${code}-${occurrence}`,
      event: 'CUSTOM',
      data: { type: 'CUSTOM', name: 'notice', value: { code, occurrence }, runId: 'run-1' },
    });
    for (const [code, n] of [['engine_retired', 1], ['engine_continued', 1], ['engine_retired', 2], ['engine_retired', 2]] as const) {
      handle(frame(code, n) as never);
    }
    expect(parts.filter((p) => p.type === 'notice').map((p) => `${p.code}:${p.occurrence}`)).toEqual([
      'engine_retired:1', 'engine_continued:1', 'engine_retired:2',
    ]);
    render(<Theme><RunNotices parts={parts} /></Theme>);
    expect(screen.getAllByRole('note').map((n) => n.textContent)).toEqual([i18n.t('agentActivity.notice.engineRetired')]);
  });

  it('G48-F4: a task queued for a free sandbox says roughly when it tries again', () => {
    const now = Date.parse('2026-11-06T12:00:00.000Z');
    expect(sandboxWaitText('2026-11-06T12:00:30.000Z', now)).toBe('Queued for a free sandbox, trying again in 30 s…');
    expect(sandboxWaitText(undefined, now)).toBe(i18n.t('chatStream.sandboxWait'));
  });
});
