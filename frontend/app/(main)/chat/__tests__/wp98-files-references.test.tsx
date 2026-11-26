/**
 * WP98-UI-1..3 (ADR-RUN-01 D2): `/files` shows the person's files, and a plain line for each fetched copy
 * ("onyx-dot-app/onyx at 57cb7b8 — fetched copy, not saved") that is never a file to open or download.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import en from '@/lib/i18n/locales/en-US.json';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

import { FilesBody, ReferenceLines } from '../components/message-area/sandbox-result-rows';
import type { WorkspaceFileList, WorkspaceReference } from '../types';
import type { BuiltinRow } from '../builtin-results';

const ONYX: WorkspaceReference = {
  path: 'repos/onyx',
  label: 'onyx-dot-app/onyx',
  address: 'https://github.com/onyx-dot-app/onyx',
  version: '57cb7b8e0f12a9',
};
const OLD: WorkspaceReference = { path: 'repos/app', label: 'acme/app', address: 'https://github.com/acme/app' };

const list = (over: Partial<WorkspaceFileList>): WorkspaceFileList => ({
  workspaceId: 'ws:conv-1',
  revision: 3,
  createdAt: Date.now(),
  files: [{ path: 'out/report.md', sizeBytes: 140_000 }],
  totalBytes: 140_000,
  omitted: 0,
  ...over,
});

function Files({ files }: { files: WorkspaceFileList }) {
  const row = { id: 'r1', afterPairs: 1, typed: '/files', result: { kind: 'files' } } as unknown as BuiltinRow;
  return (
    <Theme>
      <FilesBody
        result={{ kind: 'files', list: files, session: { agentId: 'a', sessionId: 'conv-1' }, running: false }}
        row={row}
        rowKey="slot-1"
      />
    </Theme>
  );
}

afterEach(cleanup);

describe('WP-98: references in /files', () => {
  it('WP98-UI-1: each reference is one plain line, with its commit when it has one', () => {
    render(
      <Theme>
        <ReferenceLines references={[ONYX, OLD]} />
      </Theme>,
    );
    expect(screen.getByTestId('sandbox-reference-repos/onyx').textContent).toBe(
      'onyx-dot-app/onyx at 57cb7b8 — fetched copy, not saved',
    );
    expect(screen.getByTestId('sandbox-reference-repos/app').textContent).toBe('acme/app — fetched copy, not saved');
  });

  it('WP98-UI-2: the files stay a tree to open, and a reference is never among them', () => {
    render(<Files files={list({ references: [ONYX] })} />);
    const tree = screen.getByTestId('sandbox-files-tree');
    expect(within(tree).queryByText(/onyx/)).toBeNull();
    expect(within(tree).getByText('report.md')).toBeTruthy();
    expect(screen.getByTestId('sandbox-files-references').textContent).toContain('fetched copy, not saved');
  });

  it('WP98-UI-3: a workspace holding only a reference still says what it holds', () => {
    render(<Files files={list({ files: [], totalBytes: 0, references: [ONYX] })} />);
    expect(screen.queryByText(en.slashCommands.reason.noWorkspace)).toBeNull();
    expect(screen.getByTestId('sandbox-reference-repos/onyx')).toBeTruthy();
  });
});
