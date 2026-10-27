import { describe, it, expect } from 'vitest';
import type { CitationApiResponse } from '@/chat/types';
import type { StreamingCitationData } from '../types';
import { buildCitationMapsFromApi, buildCitationMapsFromStreaming } from '../utils';

// `connector` names the kind of source (JIRA); `connectorId` says which
// instance, which is how a demo connector's records are told from real ones.

const metadata = {
  recordName: 'INC-2031',
  recordId: 'r1',
  connector: 'JIRA',
  recordType: 'TICKET',
  mimeType: 'text/markdown',
  extension: 'md',
  previewRenderable: true,
  orgId: 'o1',
  recordVersion: 0,
};

describe('citation connector instance', () => {
  it('is kept from a saved answer', () => {
    const raw = [
      {
        citationId: 'c1',
        citationData: {
          _id: 'c1',
          content: 'Synchronised retries overloaded the provider.',
          chunkIndex: 1,
          citationType: 'vectordb|document',
          metadata: { ...metadata, connectorId: 'demo-1' },
        },
      },
    ] as unknown as CitationApiResponse[];

    const maps = buildCitationMapsFromApi(raw);

    expect(maps.citations.c1.connectorId).toBe('demo-1');
    expect(maps.citations.c1.connector).toBe('JIRA');
  });

  it('is kept while an answer streams', () => {
    const raw = [
      {
        content: 'Synchronised retries overloaded the provider.',
        chunkIndex: 1,
        citationType: 'vectordb|document',
        metadata: { ...metadata, origin: 'CONNECTOR', blockText: '', blockType: 'text', connectorId: 'demo-1', hideWeburl: false },
      },
    ] as unknown as StreamingCitationData[];

    const maps = buildCitationMapsFromStreaming(raw);

    expect(maps.citations['streaming-1'].connectorId).toBe('demo-1');
  });

  it('is simply absent on answers saved before it was stored', () => {
    const raw = [
      {
        citationId: 'old',
        citationData: { _id: 'old', content: 'x', chunkIndex: 2, citationType: 'vectordb|document', metadata },
      },
    ] as unknown as CitationApiResponse[];

    expect(buildCitationMapsFromApi(raw).citations.old.connectorId).toBeUndefined();
  });
});
