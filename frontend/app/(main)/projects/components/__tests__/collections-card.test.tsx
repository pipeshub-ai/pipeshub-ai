import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { ProjectKnowledgeScope } from '@/chat/project-types';
import type { AppliedFilters } from '@/chat/types';

const api = vi.hoisted(() => ({ listCollectionsForChat: vi.fn(), getNodeChildren: vi.fn() }));

vi.mock('@/chat/api', () => ({
  ChatApi: { listCollectionsForChat: (...args: unknown[]) => api.listCollectionsForChat(...args) },
}));

vi.mock('@/app/(main)/knowledge-base/api', () => ({
  KnowledgeHubApi: {
    getNodeChildren: (...args: unknown[]) => api.getNodeChildren(...args),
  },
}));

vi.mock('@/app/components/ui/MaterialIcon', () => ({
  MaterialIcon: () => null,
}));

import { CollectionsCard } from '../collections-card';

const root = (id: string, name: string, hasChildren = true, origin = 'COLLECTION') => ({
  id,
  name,
  nodeType: 'app',
  hasChildren,
  origin,
  connector: 'KB',
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('CollectionsCard', () => {
  beforeEach(() => {
    api.listCollectionsForChat.mockReset();
    api.getNodeChildren.mockReset();
  });

  it('renders loading state then empty state when no collections exist', async () => {
    api.listCollectionsForChat.mockResolvedValue({ knowledgeBases: [], nextCursor: null });

    render(
      <Theme>
        <CollectionsCard
          knowledgeScope={{ apps: [], kb: [] }}
          appliedFilters={{ apps: [], kb: [] }}
          canEdit={true}
          onChange={vi.fn()}
        />
      </Theme>,
    );

    expect(screen.getByText(/loading/i)).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText('No collections to add yet.')).toBeTruthy();
    });

    expect(api.listCollectionsForChat).toHaveBeenCalledWith(
      expect.objectContaining({ origins: 'COLLECTION' }),
    );
  });

  it('renders error message when fetching collections fails', async () => {
    api.listCollectionsForChat.mockRejectedValue(new Error('Network error'));

    render(
      <Theme>
        <CollectionsCard
          knowledgeScope={{ apps: [], kb: [] }}
          appliedFilters={{ apps: [], kb: [] }}
          canEdit={true}
          onChange={vi.fn()}
        />
      </Theme>,
    );

    await waitFor(() => {
      expect(screen.getByText('Failed to load collections')).toBeTruthy();
    });
  });

  it('renders collections list and selects a collection', async () => {
    api.listCollectionsForChat.mockResolvedValue({
      knowledgeBases: [
        root('col-1', 'Product Specs'),
        root('col-2', 'Engineering Wiki'),
      ],
      nextCursor: null,
    });

    const onChange = vi.fn();
    const initialScope: ProjectKnowledgeScope = {
      apps: ['app-1'],
      kb: [],
    };
    const initialFilters: AppliedFilters = {
      apps: [{ id: 'app-1', name: 'GitHub', nodeType: 'app', connector: 'github' }],
      kb: [],
    };

    render(
      <Theme>
        <CollectionsCard
          knowledgeScope={initialScope}
          appliedFilters={initialFilters}
          canEdit={true}
          onChange={onChange}
        />
      </Theme>,
    );

    await waitFor(() => {
      expect(screen.getByText('Product Specs')).toBeTruthy();
      expect(screen.getByText('Engineering Wiki')).toBeTruthy();
    });

    // Checkboxes are rendered for root items
    const checkboxes = screen.getAllByRole('checkbox');
    expect(checkboxes.length).toBeGreaterThanOrEqual(2);

    // Click on the first collection checkbox
    fireEvent.click(checkboxes[0]);

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        knowledgeScope: expect.objectContaining({
          apps: ['app-1'], // Preserves existing apps
          kb: ['col-1'],
        }),
        appliedFilters: expect.objectContaining({
          apps: initialFilters.apps, // Preserves existing apps
          kb: [
            expect.objectContaining({
              id: 'col-1',
              name: 'Product Specs',
              nodeType: 'app',
              connector: 'KB',
            }),
          ],
        }),
      }),
    );
  });

  it('renders collection as checked when its ID is present in knowledgeScope.kb', async () => {
    api.listCollectionsForChat.mockResolvedValue({
      knowledgeBases: [root('col-1', 'Product Specs')],
      nextCursor: null,
    });

    render(
      <Theme>
        <CollectionsCard
          knowledgeScope={{ apps: [], kb: ['col-1'] }}
          appliedFilters={{
            apps: [],
            kb: [{ id: 'col-1', name: 'Product Specs', nodeType: 'app', connector: 'KB' }],
          }}
          canEdit={true}
          onChange={vi.fn()}
        />
      </Theme>,
    );

    await waitFor(() => {
      expect(screen.getByText('Product Specs')).toBeTruthy();
    });

    const checkbox = screen.getByRole('checkbox');
    expect(checkbox.getAttribute('data-state') === 'checked' || checkbox.getAttribute('aria-checked') === 'true').toBe(true);
  });
});
