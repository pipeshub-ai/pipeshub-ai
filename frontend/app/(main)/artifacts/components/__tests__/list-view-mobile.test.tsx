import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import type { ArtifactListItem } from '../../types';
import { ArtifactsListView } from '../artifacts-list-view';

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => mobile.value }));
vi.mock('@/app/components/ui', () => ({ FileIcon: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const item: ArtifactListItem = {
  artifactId: 'a1',
  name: 'revenue-q3.png',
  artifactType: 'CHART',
  version: 1,
  sizeInBytes: 20480,
  conversationId: 'c1',
  conversationTitle: 'Q3 plan',
};

function renderList() {
  render(
    <Theme>
      <ArtifactsListView
        items={[item]}
        sortBy="createdAtTimestamp"
        sortOrder="desc"
        onSort={vi.fn()}
        onPreview={vi.fn()}
        onDownload={vi.fn()}
        onOpenChat={vi.fn()}
        pagination={{ page: 1, limit: 50, totalCount: 1, totalPages: 1 }}
        onPageChange={vi.fn()}
      />
    </Theme>,
  );
}

afterEach(() => {
  cleanup();
  mobile.value = false;
});

describe('artifacts list at phone width', () => {
  it('desktop keeps the type, conversation and size columns', () => {
    renderList();
    expect(screen.getByRole('button', { name: /Conversation/ })).toBeTruthy();
    expect(screen.getByText('Q3 plan')).toBeTruthy();
    expect(screen.getByText('CHART')).toBeTruthy();
  });

  it('a phone keeps name, date and actions, with type and size under the name', () => {
    mobile.value = true;
    renderList();
    expect(screen.queryByRole('button', { name: /Conversation/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Size/ })).toBeNull();
    expect(screen.queryByText('Q3 plan')).toBeNull();
    expect(screen.getByText('revenue-q3.png')).toBeTruthy();
    expect(screen.getByText(/CHART · 20(\.0)? KB/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Open in chat' })).toBeTruthy();
  });
});
