import React, { useSyncExternalStore } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import type { ArtifactListItem } from '../types';
import { useArtifactsStore } from '../store';
import ArtifactsPage from '../page';

// jsdom has no matchMedia; the artifacts header uses it for the mobile layout.
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as never;
Element.prototype.scrollIntoView ??= () => {};

Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }),
});

const search = vi.hoisted(() => ({ params: new URLSearchParams() }));
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }));

vi.mock('next/navigation', () => ({
  useRouter: () => router,
  usePathname: () => '/artifacts',
  useSearchParams: () =>
    useSyncExternalStore(
      () => () => {},
      () => search.params,
    ),
}));

const api = vi.hoisted(() => ({
  list: vi.fn(),
  listVersions: vi.fn(),
  delete: vi.fn(),
  streamRecord: vi.fn(),
  streamDownloadRecord: vi.fn(),
}));

vi.mock('../api', () => ({
  ArtifactsApi: { list: api.list, listVersions: api.listVersions, delete: api.delete, get: vi.fn() },
}));

vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeBaseApi: { streamRecord: api.streamRecord, streamDownloadRecord: api.streamDownloadRecord },
}));

vi.mock('@/app/components/file-preview', () => ({
  FilePreviewSidebar: ({ file, onDownload }: { file: { name: string }; onDownload?: () => void }) => (
    <section aria-label="File preview">
      <p>previewing:{file.name}</p>
      <button type="button" onClick={onDownload}>Download preview</button>
    </section>
  ),
}));

const OWNED: ArtifactListItem = {
  artifactId: 'art-1',
  name: 'report.csv',
  artifactType: 'DATA_FILE',
  mimeType: 'text/csv',
  version: 3,
  role: 'OWNER',
  conversationId: 'conv-1',
  conversationTitle: 'Quarterly numbers',
};

beforeEach(() => {
  useArtifactsStore.setState({ items: [], page: 1, search: '', artifactTypes: [], viewMode: 'list', isLoading: false, error: null });
  api.listVersions.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function openInChat(item: ArtifactListItem) {
  api.list.mockResolvedValue({ items: [item], pagination: { page: 1, limit: 50, totalCount: 1, totalPages: 1 } });
  render(
    <Theme>
      <ArtifactsPage />
    </Theme>,
  );
  await screen.findByRole('button', { name: item.name });
  const trigger = screen.getByRole('button', { name: `More options for ${item.name}` });
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
  fireEvent.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: /Open in chat$/ }));
}

describe('opening an artifact in its chat', () => {
  it('opens an assistant chat by conversation id', async () => {
    await openInChat(OWNED);
    expect(router.push).toHaveBeenCalledWith('/chat/?conversationId=conv-1');
  });

  it('opens an agent chat under its agent', async () => {
    await openInChat({ ...OWNED, agentKey: 'agent-7' });
    expect(router.push).toHaveBeenCalledWith('/chat/?agentId=agent-7&conversationId=conv-1');
  });

  it('opens a project chat inside its project', async () => {
    await openInChat({ ...OWNED, projectId: 'proj-1' });
    expect(router.push).toHaveBeenCalledWith('/chat/?projectId=proj-1&conversationId=conv-1');
  });

  it('opens an archived chat from the archived chats page', async () => {
    await openInChat({ ...OWNED, conversationArchived: true });
    expect(router.push).toHaveBeenCalledWith('/workspace/archived-chats/?conversationId=conv-1');
  });

  it('opens an archived agent chat from the archived chats page with its agent', async () => {
    await openInChat({ ...OWNED, agentKey: 'agent-7', conversationArchived: true });
    expect(router.push).toHaveBeenCalledWith(
      '/workspace/archived-chats/?conversationId=conv-1&agentKey=agent-7',
    );
  });
});
