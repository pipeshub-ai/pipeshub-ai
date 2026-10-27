import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import type { SearchResultItem } from '@/chat/types';
import { SearchResultCard } from '../search-result-card';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

afterEach(() => cleanup());

describe('SearchResultCard', () => {
  it('says when the record last synced', () => {
    const item = {
      score: 0.9,
      citationType: 'vectordb|document',
      content: 'Quarterly numbers',
      metadata: {
        orgId: 'org-1',
        recordId: 'rec-1',
        virtualRecordId: 'vr-1',
        recordName: 'Q3 report',
        recordType: 'FILE',
        origin: 'CONNECTOR',
        connector: 'DRIVE',
        updatedAt: new Date(Date.now() - 3 * 3600_000).toISOString(),
      },
    } as SearchResultItem;

    render(
      <Theme>
        <SearchResultCard result={item} onOpenSource={vi.fn()} onPreview={vi.fn()} />
      </Theme>,
    );

    expect(screen.getByText('Synced 3h ago')).toBeTruthy();
  });
});
