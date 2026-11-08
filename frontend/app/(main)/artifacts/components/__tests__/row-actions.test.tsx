import React from 'react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import type { ArtifactListItem } from '../../types';
import { ArtifactsListView } from '../artifacts-list-view';
import { ArtifactsGridView } from '../artifacts-grid-view';

vi.mock('@/app/components/ui', () => ({ FileIcon: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
vi.mock('../artifact-thumbnail', () => ({ ArtifactThumbnail: () => null }));
const mobile = vi.hoisted(() => ({ value: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.value, useIsBelowDesktop: () => mobile.value }));

const PAGINATION = { page: 1, limit: 50, totalCount: 3, totalPages: 1 };

function artifact(overrides: Partial<ArtifactListItem>): ArtifactListItem {
  return { artifactId: 'a1', name: 'report.csv', artifactType: 'DATA_FILE', version: 1, sizeInBytes: 2048, ...overrides };
}

const owned = artifact({ artifactId: 'a1', name: 'mine.csv', role: 'OWNER', conversationId: 'c1', conversationTitle: 'Q3 numbers', version: 3 });
const shared = artifact({ artifactId: 'a2', name: 'shared.csv', role: 'READER', conversationId: 'c2', conversationTitle: 'Team chat' });
const orphan = artifact({ artifactId: 'a3', name: 'legacy.csv', role: null, conversationId: 'c3' });

type Handlers = Record<'onPreview' | 'onDownload' | 'onOpenChat' | 'onShare' | 'onDelete', ReturnType<typeof vi.fn>>;
const handlers = (): Handlers => ({ onPreview: vi.fn(), onDownload: vi.fn(), onOpenChat: vi.fn(), onShare: vi.fn(), onDelete: vi.fn() });

const views = {
  list: (h: Handlers) => (
    <ArtifactsListView
      items={[owned, shared, orphan]}
      sortBy="createdAtTimestamp"
      sortOrder="desc"
      onSort={vi.fn()}
      pagination={PAGINATION}
      onPageChange={vi.fn()}
      {...h}
    />
  ),
  grid: (h: Handlers) => <ArtifactsGridView items={[owned, shared, orphan]} pagination={PAGINATION} onPageChange={vi.fn()} {...h} />,
};

beforeAll(() => {
  // Radix menus measure and observe on open; jsdom has neither.
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as never;
  Element.prototype.scrollIntoView ??= () => {};
});

afterEach(() => {
  cleanup();
  mobile.value = false;
});

async function openMenuFor(name: string) {
  const trigger = screen.getByRole('button', { name: `More options for ${name}` });
  await act(async () => {
    trigger.focus();
    trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  });
  return screen.getByRole('menu');
}

const itemNames = (menu: HTMLElement) => within(menu).getAllByRole('menuitem').map((el) => el.textContent?.trim());

describe.each(Object.entries(views))('%s view row actions', (_name, view) => {
  it('puts the actions behind one menu per artifact instead of separate buttons', () => {
    render(<Theme>{view(handlers())}</Theme>);

    expect(screen.getAllByRole('button', { name: /^More options for / })).toHaveLength(3);
    expect(screen.queryByRole('button', { name: 'Download' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
  });

  it("offers the owner preview, download, open in chat, share and delete, delete last", async () => {
    render(<Theme>{view(handlers())}</Theme>);

    expect(itemNames(await openMenuFor('mine.csv'))).toEqual(['Preview', 'Download', 'Open in chat', 'Share', 'Delete']);
  });

  it('offers no share or delete to a viewer, and no open in chat for a chat the caller cannot see', async () => {
    render(<Theme>{view(handlers())}</Theme>);
    expect(itemNames(await openMenuFor('shared.csv'))).toEqual(['Preview', 'Download', 'Open in chat']);
    cleanup();

    render(<Theme>{view(handlers())}</Theme>);
    expect(itemNames(await openMenuFor('legacy.csv'))).toEqual(['Preview', 'Download']);
  });

  it.each([
    ['Preview', 'onPreview'],
    ['Download', 'onDownload'],
    ['Open in chat', 'onOpenChat'],
    ['Share', 'onShare'],
    ['Delete', 'onDelete'],
  ] as const)('%s calls its handler with the artifact, and nothing else', async (label, handler) => {
    const h = handlers();
    render(<Theme>{view(h)}</Theme>);

    fireEvent.click(within(await openMenuFor('mine.csv')).getByRole('menuitem', { name: label }));

    expect(h[handler]).toHaveBeenCalledWith(owned);
    for (const [key, fn] of Object.entries(h)) {
      if (key !== handler) expect(fn, key).not.toHaveBeenCalled();
    }
  });

  it('opens the preview from the row itself', () => {
    const h = handlers();
    render(<Theme>{view(h)}</Theme>);

    fireEvent.click(screen.getByRole('button', { name: 'mine.csv' }));

    expect(h.onPreview).toHaveBeenCalledWith(owned);
  });

  it('marks artifacts with more than one version and shows translated types', () => {
    render(<Theme>{view(handlers())}</Theme>);

    expect(screen.getByText('v3')).toBeTruthy();
    expect(screen.queryByText('v1')).toBeNull();
    expect(screen.getAllByText(/Data file/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/DATA_FILE/)).toBeNull();
  });

  it('says which items of how many are shown', () => {
    render(<Theme>{view(handlers())}</Theme>);

    expect(screen.getByText('Showing 1-3 of 3')).toBeTruthy();
    expect((screen.getByRole('button', { name: /Previous/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('list view on a phone', () => {
  it('drops the side columns and shows type, size and chat under the name', () => {
    mobile.value = true;
    render(<Theme>{views.list(handlers())}</Theme>);

    expect(screen.queryByText('Conversation')).toBeNull();
    expect(screen.queryByText('Size')).toBeNull();
    const row = screen.getByRole('button', { name: 'mine.csv' });
    expect(within(row).getByText(/Data file · 2.0 KB · Q3 numbers/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'More options for mine.csv' })).toBeTruthy();
  });
});

describe('list view headers', () => {
  it('only sortable columns are buttons', () => {
    render(<Theme>{views.list(handlers())}</Theme>);

    expect(screen.getByRole('button', { name: /^Name/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Conversation/ })).toBeNull();
    expect(screen.getByText('Conversation')).toBeTruthy();
  });
});
