import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';
import fs from 'node:fs';
import path from 'node:path';

import en from '@/lib/i18n/locales/en-US.json';
import type { CitationApiResponse } from '@/chat/types';
import type { StreamingCitationData } from '../types';
import { buildCitationMapsFromApi, buildCitationMapsFromStreaming } from '../utils';
import { InlineCitationBadge } from '../inline-citation-badge';
import { CitationsTab } from '../citations-tab';

afterEach(() => cleanup());

// jsdom has no matchMedia; the reference cards' useIsMobile needs it.
Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }),
});

const LABEL = en.chat.citationUnavailable;

const readable = {
  citationId: 'c1',
  citationData: {
    _id: 'c1',
    content: 'Revenue grew 12%.',
    chunkIndex: 1,
    citationType: 'vectordb|document',
    metadata: {
      recordName: 'Q3 report.pdf',
      recordId: 'r1',
      connector: 'DRIVE',
      recordType: 'FILE',
      mimeType: 'application/pdf',
      extension: 'pdf',
      previewRenderable: true,
      orgId: 'o1',
      recordVersion: 0,
      webUrl: 'https://files.test/q3',
    },
  },
};
// What Node sends a shared viewer for a record they cannot read (P1b C-6).
const withheld = { citationId: 'unavailable-1', citationData: { chunkIndex: 2, unavailable: true } };

describe('a citation the shared viewer cannot read', () => {
  it('keeps its number from a saved answer, and is no source', () => {
    const maps = buildCitationMapsFromApi([readable, withheld] as unknown as CitationApiResponse[]);
    expect(maps.citationsOrder[2]).toBe('unavailable-1');
    expect(maps.citations['unavailable-1']).toMatchObject({ chunkIndex: 2, unavailable: true, recordId: '', recordName: '', content: '' });
    expect(maps.citations['unavailable-1'].webUrl).toBeUndefined();
    expect(maps.sourcesOrder).toEqual(['r1']);
  });

  it('keeps its number from the live stream, and is no source', () => {
    const maps = buildCitationMapsFromStreaming([
      { chunkIndex: 3, unavailable: true },
    ] as unknown as StreamingCitationData[]);
    expect(maps.citations['streaming-3']).toMatchObject({ chunkIndex: 3, unavailable: true });
    expect(maps.sourcesOrder).toEqual([]);
  });

  it('renders inline as "source not available to you", with no link', () => {
    const maps = buildCitationMapsFromApi([withheld] as unknown as CitationApiResponse[]);
    render(
      <Theme>
        <InlineCitationBadge chunkIndex={2} citation={maps.citations['unavailable-1']} />
      </Theme>,
    );
    const badge = screen.getByTestId('citation-unavailable');
    expect(badge.textContent).toContain(LABEL);
    expect(badge.textContent).toContain('2');
    expect(badge.querySelector('a')).toBeNull();
  });

  it('renders in the Citations tab beside the readable ones', () => {
    const maps = buildCitationMapsFromApi([readable, withheld] as unknown as CitationApiResponse[]);
    render(
      <Theme>
        <CitationsTab citationMaps={maps} />
      </Theme>,
    );
    expect(screen.getByTestId('citation-unavailable-card').textContent).toContain(LABEL);
    expect(screen.getAllByText(/Q3 report/).length).toBeGreaterThan(0);
  });

  it('is translated in every locale', () => {
    const dir = path.resolve(__dirname, '../../../../../../../../lib/i18n/locales');
    const locales = fs.readdirSync(dir).filter((f) => f.endsWith('.json'));
    expect(locales.length).toBeGreaterThan(1);
    for (const file of locales) {
      const locale = JSON.parse(fs.readFileSync(path.join(dir, file), 'utf8')) as { chat: Record<string, unknown> };
      const text = locale.chat.citationUnavailable;
      expect(typeof text === 'string' && text.length > 0, file).toBe(true);
    }
  });
});
