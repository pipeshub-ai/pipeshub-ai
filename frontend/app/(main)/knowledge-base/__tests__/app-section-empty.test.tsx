import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';
import type { KnowledgeHubNode } from '../types';

vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

import { AppSection } from '../sidebar/section';

afterEach(cleanup);

vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });

const app = {
  id: 'app-1', name: 'Engineering Handbook', nodeType: 'app', parentId: null, origin: 'COLLECTION',
  connector: 'KB', hasChildren: true, permission: { role: 'OWNER', canEdit: true, canDelete: true },
  sharingStatus: 'private',
} as unknown as KnowledgeHubNode;

const noop = async () => {};

describe('rail row of an app whose records are not sub-folders', () => {
  it('does not say "No items" when it has no sub-folders: its records are in the table', () => {
    render(
      <Theme>
        <AppSection
          app={app}
          childNodes={[]}
          isLoading={false}
          isExpanded
          onFolderSelect={() => {}}
          onFolderExpand={noop}
          onToggleFolderExpanded={() => {}}
          expandedFolders={{}}
          loadingNodeIds={new Set()}
        />
      </Theme>,
    );

    expect(screen.getByText('Engineering Handbook')).toBeTruthy();
    expect(screen.queryByText('No items')).toBeNull();
  });
});
