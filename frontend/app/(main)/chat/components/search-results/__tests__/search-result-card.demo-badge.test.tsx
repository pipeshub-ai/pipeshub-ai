import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import { useDemoDataStore } from '@/app/(main)/workspace/connectors/demo-data/store';
import type { SearchResultItem } from '@/chat/types';
import { SearchResultCard } from '../search-result-card';

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: () => undefined },
  useTranslation: () => ({
    t: (key: string) => {
      let cur: unknown = en;
      for (const part of key.split('.')) {
        if (typeof cur !== 'object' || cur === null || !(part in cur)) return key;
        cur = (cur as Record<string, unknown>)[part];
      }
      return typeof cur === 'string' ? cur : key;
    },
  }),
}));

vi.mock('@/lib/hooks/use-is-mobile', () => ({ useIsMobile: () => false }));

const DEMO = { _key: 'demo-1', type: 'Demo', name: 'Acme Corp demo data', isActive: true } as Connector;

// A demo record imitates a real system, so its connector type alone looks real.
function result(connectorId: string | undefined): SearchResultItem {
  return {
    score: 0.9,
    citationType: 'vectordb|document',
    content: 'On-call over a public holiday is voluntary.',
    metadata: {
      orgId: 'org-1',
      recordId: 'rec-1',
      virtualRecordId: 'vr-1',
      recordName: 'Engineering on-call handbook',
      recordType: 'FILE',
      origin: 'CONNECTOR',
      connector: 'DRIVE',
      connectorId,
    },
  } as SearchResultItem;
}

function renderCard(item: SearchResultItem) {
  return render(
    <Theme>
      <SearchResultCard result={item} onOpenSource={vi.fn()} onPreview={vi.fn()} />
    </Theme>,
  );
}

afterEach(() => {
  cleanup();
  useDemoDataStore.getState().reset();
});

describe('SearchResultCard demo badge', () => {
  it('marks a result from the Demo connector', () => {
    useDemoDataStore.setState({ demoConnectors: [DEMO] });
    renderCard(result('demo-1'));
    expect(screen.getByText(en.demoData.badge.label)).toBeTruthy();
  });

  it('leaves a result from a real connector unmarked', () => {
    useDemoDataStore.setState({ demoConnectors: [DEMO] });
    renderCard(result('drive-1'));
    expect(screen.queryByText(en.demoData.badge.label)).toBeNull();
  });
});
