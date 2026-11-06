import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import '@/lib/__tests__/test-i18n';

import type { SearchResultItem } from '@/chat/types';
import { SearchResultCard } from '../search-result-card';

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

afterEach(() => cleanup());

const LONG = "alex.morgan@pipeshub-test.com's Private planning collection for every quarter of the year";

describe('SearchResultCard header', () => {
  it('keeps a gap between a long, truncated record name and its buttons, with the full name as a tooltip', () => {
    const item = {
      score: 0.9,
      citationType: 'vectordb|document',
      content: 'Quarterly numbers',
      metadata: {
        orgId: 'org-1',
        recordId: 'rec-1',
        virtualRecordId: 'vr-1',
        recordName: LONG,
        recordType: 'FILE',
        origin: 'UPLOAD',
        connector: 'KB',
        webUrl: 'https://example.test/rec-1',
      },
    } as SearchResultItem;

    render(
      <Theme>
        <SearchResultCard result={item} onOpenSource={vi.fn()} onPreview={vi.fn()} />
      </Theme>,
    );

    const label = screen.getByText(LONG);
    expect(label.getAttribute('title')).toBe(LONG);
    expect(label.style.whiteSpace).toBe('nowrap');
    const row = label.parentElement!.parentElement!;
    expect(row.className).toMatch(/rt-r-gap-2/);
  });
});
