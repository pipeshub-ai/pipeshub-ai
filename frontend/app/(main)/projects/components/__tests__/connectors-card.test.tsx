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

import { ConnectorsCard } from '../connectors-card';
import { computeScopeCounts } from '../settings-panel';
import { createSerialSaver } from '../../utils/serial-saver';

const connectorItem = (id: string, name: string, connector: string, origin = 'CONNECTOR') => ({
  id,
  name,
  nodeType: 'app',
  hasChildren: true,
  origin,
  connector,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('computeScopeCounts', () => {
  it('returns zeros when scope and filters are undefined', () => {
    expect(computeScopeCounts(undefined, undefined)).toEqual({
      connectorCount: 0,
      collectionCount: 0,
    });
  });

  it('counts whole collections in kb under collectionCount', () => {
    const scope: ProjectKnowledgeScope = { apps: [], kb: ['col-1', 'col-2'] };
    const filters: AppliedFilters = {
      apps: [],
      kb: [
        { id: 'col-1', name: 'Col 1', nodeType: 'app', connector: 'KB' },
        { id: 'col-2', name: 'Col 2', nodeType: 'app', connector: 'KB' },
      ],
    };
    expect(computeScopeCounts(scope, filters)).toEqual({
      connectorCount: 0,
      collectionCount: 2,
    });
  });

  it('counts collection folders and documents under collectionCount and excludes them from connectorCount', () => {
    const scope: ProjectKnowledgeScope = {
      apps: [],
      kb: [],
      recordGroups: ['folder-in-kb'],
      records: ['doc-in-kb'],
    };
    const filters: AppliedFilters = {
      apps: [],
      kb: [],
      recordGroups: [{ id: 'folder-in-kb', name: 'Folder 1', nodeType: 'recordGroup', connector: 'KB' }],
      records: [{ id: 'doc-in-kb', name: 'Doc 1', nodeType: 'folder', connector: 'KB' }],
    };

    // Ticking 1 folder and 1 doc inside a collection must count as 2 Collections and 0 Connectors
    expect(computeScopeCounts(scope, filters)).toEqual({
      connectorCount: 0,
      collectionCount: 2,
    });
  });

  it('correctly splits mixed selections between Connectors and Collections', () => {
    const scope: ProjectKnowledgeScope = {
      apps: ['app-github'],
      kb: ['col-wiki'],
      recordGroups: ['folder-in-kb', 'repo-in-github'],
      records: ['issue-in-github'],
    };
    const filters: AppliedFilters = {
      apps: [{ id: 'app-github', name: 'GitHub', nodeType: 'app', connector: 'github' }],
      kb: [{ id: 'col-wiki', name: 'Wiki', nodeType: 'app', connector: 'KB' }],
      recordGroups: [
        { id: 'folder-in-kb', name: 'KB Folder', nodeType: 'recordGroup', connector: 'KB' },
        { id: 'repo-in-github', name: 'Repo', nodeType: 'recordGroup', connector: 'github' },
      ],
      records: [{ id: 'issue-in-github', name: 'Issue 1', nodeType: 'record', connector: 'github' }],
    };

    // Collections: 1 whole collection + 1 KB folder = 2
    // Connectors: 1 app + 1 repo + 1 issue = 3
    expect(computeScopeCounts(scope, filters)).toEqual({
      connectorCount: 3,
      collectionCount: 2,
    });
  });
});

describe('ConnectorsCard', () => {
  beforeEach(() => {
    api.listCollectionsForChat.mockReset();
    api.getNodeChildren.mockReset();
    api.getNodeChildren.mockResolvedValue({ nodes: [], breadcrumbs: [] });
  });

  it('renders loading state then empty state when no connectors exist', async () => {
    api.listCollectionsForChat.mockResolvedValue({ knowledgeBases: [], nextCursor: null });

    render(
      <Theme>
        <ConnectorsCard
          knowledgeScope={{ apps: [], kb: [] }}
          appliedFilters={{ apps: [], kb: [] }}
          canEdit={true}
          onChange={vi.fn()}
        />
      </Theme>,
    );

    expect(screen.getByText(/loading/i)).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText('No connectors to add yet.')).toBeTruthy();
    });
  });

  it('filters out collection-origin items from the connectors list', async () => {
    api.listCollectionsForChat.mockResolvedValue({
      knowledgeBases: [
        connectorItem('app-github', 'GitHub Org', 'github', 'CONNECTOR'),
        connectorItem('col-kb', 'Company Docs', 'KB', 'COLLECTION'),
      ],
      nextCursor: null,
    });

    render(
      <Theme>
        <ConnectorsCard
          knowledgeScope={{ apps: [], kb: [] }}
          appliedFilters={{ apps: [], kb: [] }}
          canEdit={true}
          onChange={vi.fn()}
        />
      </Theme>,
    );

    await waitFor(() => {
      expect(screen.getByText('GitHub Org')).toBeTruthy();
    });

    // Collection item must NOT appear in Connectors card
    expect(screen.queryByText('Company Docs')).toBeNull();
  });

  it('excludes collection-owned folders from savedNodes so they do not show in Connectors card', async () => {
    api.listCollectionsForChat.mockResolvedValue({
      knowledgeBases: [],
      nextCursor: null,
    });

    const initialScope: ProjectKnowledgeScope = {
      apps: [],
      kb: [],
      recordGroups: ['folder-in-kb', 'repo-in-github'],
      records: ['doc-in-kb', 'file-in-github'],
    };
    const initialFilters: AppliedFilters = {
      apps: [],
      kb: [],
      recordGroups: [
        { id: 'folder-in-kb', name: 'KB Folder', nodeType: 'recordGroup', connector: 'KB' },
        { id: 'repo-in-github', name: 'GitHub Repo', nodeType: 'recordGroup', connector: 'github' },
      ],
      records: [
        { id: 'doc-in-kb', name: 'KB Document', nodeType: 'folder', connector: 'KB' },
        { id: 'file-in-github', name: 'GitHub File', nodeType: 'record', connector: 'github' },
      ],
    };

    render(
      <Theme>
        <ConnectorsCard
          knowledgeScope={initialScope}
          appliedFilters={initialFilters}
          canEdit={true}
          onChange={vi.fn()}
        />
      </Theme>,
    );

    await waitFor(() => {
      expect(screen.getByText('GitHub Repo')).toBeTruthy();
      expect(screen.getByText('GitHub File')).toBeTruthy();
    });

    // Collection folders and documents must NOT show up in Connectors card
    expect(screen.queryByText('KB Folder')).toBeNull();
    expect(screen.queryByText('KB Document')).toBeNull();
  });

  it('ticks a folder inside a collection, then toggles a connector: preserves collection folder and counts match', async () => {
    api.listCollectionsForChat.mockResolvedValue({
      knowledgeBases: [connectorItem('app-github', 'GitHub Org', 'github', 'CONNECTOR')],
      nextCursor: null,
    });

    const onChange = vi.fn();

    // Initial state: user has ticked a folder inside a collection
    const initialScope: ProjectKnowledgeScope = {
      apps: [],
      kb: [],
      recordGroups: ['folder-in-collection'],
      records: [],
    };
    const initialFilters: AppliedFilters = {
      apps: [],
      kb: [],
      recordGroups: [
        {
          id: 'folder-in-collection',
          name: 'Architecture Plans',
          nodeType: 'recordGroup',
          connector: 'KB',
        },
      ],
      records: [],
    };

    // Before toggling connector, counts must be 1 Collection, 0 Connectors
    const initialCounts = computeScopeCounts(initialScope, initialFilters);
    expect(initialCounts.collectionCount).toBe(1);
    expect(initialCounts.connectorCount).toBe(0);

    render(
      <Theme>
        <ConnectorsCard
          knowledgeScope={initialScope}
          appliedFilters={initialFilters}
          canEdit={true}
          onChange={onChange}
        />
      </Theme>,
    );

    await waitFor(() => {
      expect(screen.getByText('GitHub Org')).toBeTruthy();
    });

    // Toggle the connector
    const checkboxes = screen.getAllByRole('checkbox');
    expect(checkboxes.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(checkboxes[0]);

    // Verify onChange was called
    expect(onChange).toHaveBeenCalledTimes(1);
    const updatedPatch = onChange.mock.calls[0][0];

    // Assert: folder is still in knowledgeScope.recordGroups
    expect(updatedPatch.knowledgeScope.recordGroups).toContain('folder-in-collection');

    // Assert: folder is still in appliedFilters.recordGroups with connector: 'KB'
    const preservedGroup = updatedPatch.appliedFilters.recordGroups.find(
      (rg: { id: string }) => rg.id === 'folder-in-collection',
    );
    expect(preservedGroup).toBeDefined();
    expect(preservedGroup?.connector).toBe('KB');
    expect(preservedGroup?.name).toBe('Architecture Plans');

    // Assert: newly toggled connector is in apps
    expect(updatedPatch.knowledgeScope.apps).toContain('app-github');

    // Assert: Collections and Connectors counts are correct
    const finalCounts = computeScopeCounts(updatedPatch.knowledgeScope, updatedPatch.appliedFilters);
    expect(finalCounts.collectionCount).toBe(1);
    expect(finalCounts.connectorCount).toBe(1);
  });

  it('updates confirmed baseline immediately on intermediate save so failure rolls back to it', async () => {
    let confirmedScope: ProjectKnowledgeScope = { apps: [], kb: [] };
    const savedStates: ProjectKnowledgeScope[] = [];

    const saver = createSerialSaver<ProjectKnowledgeScope, ProjectKnowledgeScope>({
      save: async (scope) => {
        // Simulates ProjectApi.update setting confirmed baseline on success
        confirmedScope = scope;
        savedStates.push(scope);
        return scope;
      },
      onSaved: (result) => {
        confirmedScope = result;
      },
      onError: () => {
        // Falls back to confirmedScope
      },
    });

    // Push initial save A
    const saveA = saver.push({ apps: ['app-a'], kb: [] });
    // Push save B while A is in flight
    const saveB = saver.push({ apps: ['app-a', 'app-b'], kb: [] });

    await Promise.all([saveA, saveB]);

    // Both A and B were processed sequentially, baseline advanced
    expect(confirmedScope).toEqual({ apps: ['app-a', 'app-b'], kb: [] });
    expect(savedStates).toEqual([
      { apps: ['app-a'], kb: [] },
      { apps: ['app-a', 'app-b'], kb: [] },
    ]);
  });
});
