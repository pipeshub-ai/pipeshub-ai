import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: ({ name }: { name: string }) => <span data-icon={name} /> }));
vi.mock('@/app/components/ui/lottie-loader', () => ({ LottieLoader: () => null }));
vi.mock('@/app/components/sidebar/sidebar-expand-button', () => ({ SidebarExpandButton: () => null }));
const projectApi = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock('@/chat/project-api', () => ({ ProjectApi: projectApi }));

import { ProjectList } from '../project-list';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('ProjectList', () => {
  it('shows a no-results state for a search that matches nothing, not the first-run empty state', async () => {
    projectApi.list.mockResolvedValue({ projects: [], pagination: { page: 1, limit: 30, totalCount: 0, totalPages: 0 } });
    render(
      <Theme>
        <ProjectList />
      </Theme>,
    );
    expect(await screen.findByText('No projects yet')).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText('Search projects…'), { target: { value: 'zebra' } });
    expect(await screen.findByText('No projects match “zebra”')).toBeTruthy();
    expect(screen.queryByText('No projects yet')).toBeNull();
    expect(screen.getByRole('button', { name: 'Clear search' })).toBeTruthy();
  });

  it('marks a pinned project with the pin a pinned chat has, and its menu leads each item with its glyph', async () => {
    globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
    const project = {
      _id: 'p1', orgId: 'o1', userId: 'u1', name: 'Launch', tools: [], visibility: 'private', chatSharing: 'private',
      isPinned: true, isArchived: false, lastActivityAt: 0, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z', role: 'owner',
    };
    projectApi.list.mockResolvedValue({ projects: [project], pagination: { page: 1, limit: 30, totalCount: 1, totalPages: 1 } });
    render(
      <Theme>
        <ProjectList />
      </Theme>,
    );
    const trigger = await screen.findByRole('button', { name: 'Actions for Launch' });
    expect(document.querySelector('[data-icon="keep"]')).not.toBeNull();
    expect(document.querySelector('[data-icon="star"]')).toBeNull();

    fireEvent.keyDown(trigger, { key: 'Enter' });
    const menu = await screen.findByRole('menu');
    expect(within(menu).getAllByRole('menuitem').map((el) => [el.textContent, el.querySelector('[data-icon]')?.getAttribute('data-icon')])).toEqual([
      ['Unpin', 'keep_off'],
      ['Archive project', 'archive'],
      ['Delete project', 'delete'],
    ]);
  });
});
