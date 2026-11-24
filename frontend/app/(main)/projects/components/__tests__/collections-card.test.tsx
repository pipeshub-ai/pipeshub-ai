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
    api.getNodeChildren.mockResolvedValue({ nodes: [], breadcrumbs: [] });
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

  it('paginates across multiple pages until nextCursor is exhausted', async () => {
    api.listCollectionsForChat
      .mockResolvedValueOnce({
        knowledgeBases: [root('col-1', 'Page 1 Collection')],
        nextCursor: 'cursor-page-2',
      })
      .mockResolvedValueOnce({
        knowledgeBases: [root('col-2', 'Page 2 Collection')],
        nextCursor: null,
      });

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
      expect(screen.getByText('Page 1 Collection')).toBeTruthy();
      expect(screen.getByText('Page 2 Collection')).toBeTruthy();
    });

    expect(api.listCollectionsForChat).toHaveBeenCalledTimes(2);
  });

  it('translates unnamed collection with untitledCollection fallback', async () => {
    api.listCollectionsForChat.mockResolvedValue({
      knowledgeBases: [root('col-blank', '   ')],
      nextCursor: null,
    });

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
      expect(screen.getByText('Untitled collection')).toBeTruthy();
    });
  });

  it('excludes connector-owned record groups from saved rows and preserves them on change', async () => {
    api.listCollectionsForChat.mockResolvedValue({
      knowledgeBases: [root('col-1', 'Engineering Wiki')],
      nextCursor: null,
    });

    const onChange = vi.fn();
    const initialScope: ProjectKnowledgeScope = {
      apps: ['app-jira'],
      kb: [],
      recordGroups: ['rg-jira-project', 'rg-kb-folder'],
      records: ['rec-jira-issue', 'rec-kb-doc'],
    };
    const initialFilters: AppliedFilters = {
      apps: [{ id: 'app-jira', name: 'Jira', nodeType: 'app', connector: 'jira' }],
      kb: [],
      recordGroups: [
        { id: 'rg-jira-project', name: 'PROJ-1', nodeType: 'recordGroup', connector: 'jira' },
        { id: 'rg-kb-folder', name: 'Architecture Docs', nodeType: 'recordGroup', connector: 'KB' },
      ],
      records: [
        { id: 'rec-jira-issue', name: 'Issue 123', nodeType: 'record', connector: 'jira' },
        { id: 'rec-kb-doc', name: 'Runbook.md', nodeType: 'folder', connector: 'KB' },
      ],
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
      expect(screen.getByText('Engineering Wiki')).toBeTruthy();
      expect(screen.getByText('Architecture Docs')).toBeTruthy();
      expect(screen.getByText('Runbook.md')).toBeTruthy();
    });

    // Connector-owned rows must NOT be shown as collection rows
    expect(screen.queryByText('PROJ-1')).toBeNull();
    expect(screen.queryByText('Issue 123')).toBeNull();

    // Toggling preserves connector-owned recordGroups and records
    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]);

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        knowledgeScope: expect.objectContaining({
          apps: ['app-jira'],
          recordGroups: expect.arrayContaining(['rg-jira-project']),
          records: expect.arrayContaining(['rec-jira-issue']),
        }),
        appliedFilters: expect.objectContaining({
          recordGroups: expect.arrayContaining([
            expect.objectContaining({ id: 'rg-jira-project', connector: 'jira' }),
          ]),
          records: expect.arrayContaining([
            expect.objectContaining({ id: 'rec-jira-issue', connector: 'jira' }),
          ]),
        }),
      }),
    );
  });

  it('preserves scoped IDs that have no matching node in appliedFilters', async () => {
    api.listCollectionsForChat.mockResolvedValue({
      knowledgeBases: [root('col-1', 'Engineering Wiki')],
      nextCursor: null,
    });

    const onChange = vi.fn();
    const initialScope: ProjectKnowledgeScope = {
      apps: [],
      kb: [],
      recordGroups: ['rg-orphan-no-node'],
      records: ['rec-orphan-no-node'],
    };
    const initialFilters: AppliedFilters = {
      apps: [],
      kb: [],
      recordGroups: [],
      records: [],
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
      expect(screen.getByText('Engineering Wiki')).toBeTruthy();
    });

    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]);

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        knowledgeScope: expect.objectContaining({
          recordGroups: expect.arrayContaining(['rg-orphan-no-node']),
          records: expect.arrayContaining(['rec-orphan-no-node']),
        }),
      }),
    );
  });
});
