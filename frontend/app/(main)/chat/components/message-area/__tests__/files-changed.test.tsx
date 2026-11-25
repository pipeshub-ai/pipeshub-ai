/**
 * CTL-4: the "Files changed" block and an engine command's row (they replace the code-task card), plus the draft
 * badge on an answer's artifact card (UI-9). Engine text renders as text; diffs and output are bounded.
 */
import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import { i18n } from '@/lib/i18n';
import { FilesChanged } from '../files-changed';
import { diffLines } from '../code-task-diff';
import { ArtifactsPanel } from '../artifacts-panel';
import { AgentActivityTimeline } from '../agent-activity';
import { changedFilesFromEngine } from '../../../engine-steps';
import type { SSEEngineProgressEvent } from '../../../types';

const h = React.createElement;

beforeAll(async () => {
  await i18n.changeLanguage('en-US');
});
afterEach(() => cleanup());

const frame = (kind: string, text = '', data: Record<string, unknown> = {}): SSEEngineProgressEvent => ({
  tool: 'code_task',
  taskId: 'ct-1',
  kind,
  text,
  data,
});

const DIFF = '--- a/out/report.md\n+++ b/out/report.md\n@@ -1,2 +1,2 @@\n # Report\n-Revenue 41\n+Revenue 42\n';
const LONG = `--- a/big.txt\n+++ b/big.txt\n@@ -0,0 +1,300 @@\n${Array.from({ length: 300 }, (_, i) => `+line ${i}`).join('\n')}\n`;

describe('Files changed', () => {
  const files = changedFilesFromEngine([
    frame('file', 'out/report.md', { change: 'modified' }),
    frame('diff', 'out/report.md', { change: 'modified', sizeBytes: 22, diff: DIFF }),
    frame('diff', 'out/chart.png', { change: 'added', sizeBytes: 2048, binary: true }),
    frame('diff', 'big.txt', { change: 'added', diff: LONG, truncated: true }),
  ]);

  it('one line per file with what changed; one diff open at a time, closed by default', () => {
    render(h(Theme, null, h(FilesChanged, { files })));
    const list = screen.getByRole('list', { name: 'Files changed' });
    const report = within(list).getByRole('button', { name: /out\/report\.md/ });
    expect(report.textContent).toContain('modified');
    expect(report.textContent).toContain('+1 −1');
    expect(report.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(report);
    const diff = screen.getByRole('region', { name: 'Changes in out/report.md' });
    const lines = [...diff.querySelectorAll('span')].map((s) => [s.getAttribute('data-kind'), s.textContent]);
    expect(lines).toContainEqual(['removed', '-Revenue 41']);
    expect(lines).toContainEqual(['added', '+Revenue 42']);
    fireEvent.click(within(list).getByRole('button', { name: /out\/chart\.png/ }));
    expect(screen.queryByRole('region', { name: 'Changes in out/report.md' })).toBeNull();
    expect(screen.getByText('Binary file, 2 KB. No text diff.')).toBeTruthy();
  });

  it('a long diff shows its first 200 lines and points at View activity', () => {
    render(h(Theme, null, h(FilesChanged, { files })));
    fireEvent.click(screen.getByRole('button', { name: /big\.txt/ }));
    const diff = screen.getByRole('region', { name: 'Changes in big.txt' });
    expect(diff.querySelectorAll('span')).toHaveLength(200);
    expect(screen.getByText('Full diff in View activity')).toBeTruthy();
  });

  it('a new file without a diff opens its preview', () => {
    const onOpen = vi.fn();
    const made = [{ path: 'report.pdf', change: 'added' as const, sizeBytes: 2048, additions: 0, deletions: 0, artifactId: 'rec-9' }];
    render(h(Theme, null, h(FilesChanged, { files: made, onOpen })));
    const row = screen.getByRole('button', { name: /report\.pdf/ });
    expect(row.textContent).toContain('added');
    expect(row.textContent).toContain('2 KB');
    fireEvent.click(row);
    expect(onOpen).toHaveBeenCalledWith(made[0]);
  });

  it('classifies diff lines by prefix', () => {
    expect(diffLines('+a\n-b\n c\n@@ x\n')).toEqual([
      { kind: 'added', text: '+a' },
      { kind: 'removed', text: '-b' },
      { kind: 'context', text: ' c' },
      { kind: 'hunk', text: '@@ x' },
    ]);
  });
});

describe('an engine command row', () => {
  it('shows the command and its output as text, escapes stripped, the last 20 lines', () => {
    const output = Array.from({ length: 30 }, (_, i) => `out ${i}`).join('\n');
    const parts = [
      {
        type: 'engine_task' as const,
        runId: 'r',
        frames: [
          frame('command', 'echo <img src=x onerror=alert(1)>', { callId: 'c1' }),
          frame('output', '\u001b[32m<script>alert(1)</script>\u001b[0m\n', { callId: 'c1' }),
          frame('output', `${output}\n`, { callId: 'c1' }),
          frame('command_done', '', { callId: 'c1', exitCode: 2 }),
        ],
      },
    ];
    const { container } = render(h(Theme, null, h(AgentActivityTimeline, { parts })));
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('echo <img src=x onerror=alert(1)>')).toBeTruthy();
    expect(screen.getByText('exit 2')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { expanded: false }));
    const shown = container.querySelector('pre')?.textContent ?? '';
    expect(shown.split('\n')).toHaveLength(20);
    expect(shown).toContain('out 29');
    expect(shown).not.toContain('\u001b');
    expect(container.querySelector('script')).toBeNull();
    fireEvent.click(screen.getByText('Show the whole output'));
    expect(container.querySelector('pre')?.textContent).toContain('out 0');
    fireEvent.click(screen.getByText('Show only the last lines'));
    expect(container.querySelector('pre')?.textContent?.split('\n')).toHaveLength(20);
  });
});

describe('the answer’s artifact card (UI-9)', () => {
  const artifact = {
    id: 'art-1',
    fileName: 'report.md',
    mimeType: 'text/markdown',
    sizeBytes: 22,
    downloadUrl: '',
    artifactType: 'DOCUMENT',
    recordId: 'art-1',
    version: 1,
  };

  it('shows a Draft badge on a draft card and none once it is final', () => {
    const view = render(h(Theme, null, h(ArtifactsPanel, { artifacts: [{ ...artifact, lifecycle: 'draft' as const }] })));
    expect(screen.getByText('Draft')).toBeTruthy();
    view.rerender(h(Theme, null, h(ArtifactsPanel, { artifacts: [{ ...artifact, version: 2 }] })));
    expect(screen.queryByText('Draft')).toBeNull();
    expect(screen.getAllByText('report.md')).toHaveLength(1);
  });
});
