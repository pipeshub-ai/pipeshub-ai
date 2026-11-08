import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));
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
});
