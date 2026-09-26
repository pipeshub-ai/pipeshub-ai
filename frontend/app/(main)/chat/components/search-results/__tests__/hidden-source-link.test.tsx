/**
 * A record whose address can't be opened (hideWeburl: the Acme demo, S3,
 * local folders) must not offer "Open in <source>" — for the demo it led to a
 * page that doesn't exist. Covers the search result card and the citation card.
 */
import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import type { SearchResultItem } from '@/chat/types';
import type { CitationData } from '../../message-area/response-tabs/citations/types';
import { SearchResultCard } from '../search-result-card';
import { ReferenceCard } from '../../message-area/response-tabs/citations/citation-card';

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: () => undefined },
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, string>) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      if (typeof cur !== 'string') return key;
      return cur.replace(/\{\{(\w+)\}\}/g, (_, name: string) => vars?.[name] ?? '');
    },
  }),
}));
vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

const ADDRESS = 'https://jira.acme-demo.example/browse/INC-2031';
const OPEN = /^Open/;

function searchResult(hideWeburl: boolean): SearchResultItem {
  return {
    score: 0.9,
    citationType: 'vectordb|document',
    content: 'Every worker retried Stripe on the same 5s tick.',
    metadata: {
      orgId: 'org-1', recordId: 'rec-1', virtualRecordId: 'vr-1',
      recordName: 'INC-2031: Synchronised billing retries', recordType: 'TICKET',
      origin: 'CONNECTOR', connector: 'JIRA', connectorId: 'demo-1',
      webUrl: ADDRESS, hideWeburl,
    },
  } as unknown as SearchResultItem;
}

function citation(hideWeburl: boolean): CitationData {
  return {
    citationId: 'c1', content: 'Every worker retried Stripe on the same 5s tick.', chunkIndex: 1,
    recordId: 'rec-1', recordName: 'INC-2031: Synchronised billing retries',
    connector: 'JIRA', connectorId: 'demo-1', recordType: 'TICKET', webUrl: ADDRESS,
    mimeType: 'text/markdown', extension: 'md', previewRenderable: true, hideWeburl,
    citationType: 'vectordb|document', origin: 'CONNECTOR',
  } as CitationData;
}

afterEach(cleanup);

describe('SearchResultCard source link', () => {
  it('offers "Open" for a record with an address it can open', () => {
    render(<Theme><SearchResultCard result={searchResult(false)} onOpenSource={vi.fn()} onPreview={vi.fn()} /></Theme>);
    expect(screen.queryAllByText(OPEN).length).toBeGreaterThan(0);
  });

  it('offers no "Open" when the address is hidden', () => {
    render(<Theme><SearchResultCard result={searchResult(true)} onOpenSource={vi.fn()} onPreview={vi.fn()} /></Theme>);
    expect(screen.queryAllByText(OPEN)).toHaveLength(0);
  });
});

describe('ReferenceCard source link', () => {
  it('offers "Open in" for a record with an address it can open', () => {
    render(<Theme><ReferenceCard citation={citation(false)} currentTab="citation" /></Theme>);
    expect(screen.queryAllByText(OPEN).length).toBeGreaterThan(0);
  });

  it('offers no "Open in" when the address is hidden', () => {
    render(<Theme><ReferenceCard citation={citation(true)} currentTab="citation" /></Theme>);
    expect(screen.queryAllByText(OPEN)).toHaveLength(0);
  });
});
