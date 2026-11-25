import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { Theme } from '@radix-ui/themes';

import en from '@/lib/i18n/locales/en-US.json';
import type { CitationApiResponse } from '@/chat/types';
import type { StreamingCitationData } from '../types';
import { buildCitationMapsFromApi, buildCitationMapsFromStreaming } from '../utils';
import { InlineCitationBadge } from '../inline-citation-badge';

afterEach(() => cleanup());

// A file an agent tool read straight from Drive (P1b C-18): the backend adds
// sourceKind and the item's external id; the rest is an ordinary citation.
const metadata = {
  recordName: 'Plan.pdf',
  recordId: 'live:0123456789abcdef0123456789abcdef',
  connector: 'DRIVE',
  connectorId: 'toolset-1',
  recordType: 'FILE',
  mimeType: 'application/pdf',
  extension: 'pdf',
  previewRenderable: false,
  orgId: 'o1',
  recordVersion: 1,
  origin: 'CONNECTOR',
  webUrl: '',
};
const provenance = { sourceKind: 'live', externalRecordId: 'file-1' };

const saved = (extra: object) =>
  [
    {
      citationId: 'c1',
      citationData: {
        _id: 'c1',
        content: 'The plan ships in May.',
        chunkIndex: 1,
        citationType: 'vectordb|document',
        metadata: { ...metadata, ...extra },
      },
    },
  ] as unknown as CitationApiResponse[];

const streamed = (extra: object) =>
  [
    {
      content: 'The plan ships in May.',
      chunkIndex: 1,
      citationType: 'vectordb|document',
      metadata: { ...metadata, blockText: '', blockType: 'text', hideWeburl: false, ...extra },
    },
  ] as unknown as StreamingCitationData[];

describe('a live citation the viewer may see (C-18)', () => {
  it('maps exactly as the same citation without its provenance, saved or streaming', () => {
    expect(buildCitationMapsFromApi(saved(provenance))).toEqual(buildCitationMapsFromApi(saved({})));
    expect(buildCitationMapsFromStreaming(streamed(provenance))).toEqual(buildCitationMapsFromStreaming(streamed({})));
  });

  it('renders as an ordinary citation, not the placeholder', () => {
    const maps = buildCitationMapsFromApi(saved(provenance));
    expect(maps.citations.c1).toMatchObject({ content: 'The plan ships in May.', recordName: 'Plan.pdf', previewRenderable: false });
    render(
      <Theme>
        <InlineCitationBadge chunkIndex={1} citation={maps.citations.c1} />
      </Theme>,
    );
    expect(screen.queryByTestId('citation-unavailable')).toBeNull();
  });

  it('when it cannot be verified for a shared viewer it is the one placeholder', () => {
    // Node sends the same shape for "denied" and "unverifiable": the browser is not told why.
    const maps = buildCitationMapsFromApi([
      { citationId: 'unavailable-0', citationData: { chunkIndex: 1, unavailable: true } },
    ] as unknown as CitationApiResponse[]);
    render(
      <Theme>
        <InlineCitationBadge chunkIndex={1} citation={maps.citations['unavailable-0']} />
      </Theme>,
    );
    expect(screen.getByTestId('citation-unavailable').textContent).toContain(en.chat.citationUnavailable);
    expect(maps.sourcesOrder).toEqual([]);
  });
});
