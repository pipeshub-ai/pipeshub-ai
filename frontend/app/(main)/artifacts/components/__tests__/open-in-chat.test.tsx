import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import '@/lib/__tests__/test-i18n';
import type { ArtifactListItem } from '../../types';
import { ArtifactsListView } from '../artifacts-list-view';
import { ArtifactsGridView } from '../artifacts-grid-view';

vi.mock('@/app/components/ui', () => ({ FileIcon: () => null }));
vi.mock('@/app/components/ui/MaterialIcon', () => ({ MaterialIcon: () => null }));

const PAGINATION = { page: 1, limit: 50, totalCount: 2, totalPages: 1 };

function artifact(overrides: Partial<ArtifactListItem>): ArtifactListItem {
  return {
    artifactId: 'a1',
    name: 'report.csv',
    artifactType: 'DATA_FILE',
    version: 1,
    ...overrides,
  };
}

const visibleChat = artifact({ artifactId: 'a1', name: 'visible.csv', conversationId: 'c1', conversationTitle: 'Q3 plan' });
const hiddenChat = artifact({ artifactId: 'a2', name: 'hidden.csv', conversationId: 'c2' });
const noChat = artifact({ artifactId: 'a3', name: 'loose.csv', conversationId: null });

const handlers = {
  onPreview: vi.fn(),
  onDownload: vi.fn(),
  onOpenChat: vi.fn(),
  onPageChange: vi.fn(),
};

function openChatButtons() {
  return screen.queryAllByRole('button', { name: 'Open in chat' });
}

afterEach(() => cleanup());

describe('Open in chat', () => {
  it('is offered in the list only for a chat the caller can see', () => {
    render(
      <Theme>
        <ArtifactsListView
          items={[visibleChat, hiddenChat, noChat]}
          sortBy="createdAtTimestamp"
          sortOrder="desc"
          onSort={vi.fn()}
          pagination={PAGINATION}
          {...handlers}
        />
      </Theme>,
    );

    expect(openChatButtons()).toHaveLength(1);
  });

  it('is offered in the grid only for a chat the caller can see', () => {
    render(
      <Theme>
        <ArtifactsGridView items={[visibleChat, hiddenChat, noChat]} pagination={PAGINATION} {...handlers} />
      </Theme>,
    );

    expect(openChatButtons()).toHaveLength(1);
  });
});
