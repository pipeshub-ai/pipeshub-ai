import { describe, it, expect } from 'vitest';
import type { CitationApiResponse } from '@/chat/types';
import type { StreamingCitationData } from '../types';
import { buildCitationMapsFromApi, buildCitationMapsFromStreaming, getConnectorConfig } from '../utils';
import { i18n } from '@/lib/i18n';

const metadata = {
  recordName: 'Q3 plan.pdf',
  recordId: 'r1',
  connector: 'SHAREPOINT ONLINE',
  recordType: 'FILE',
  mimeType: 'application/pdf',
  extension: 'pdf',
  previewRenderable: true,
  orgId: 'o1',
  recordVersion: 0,
};

function saved(meta: Record<string, unknown>, answerSavedAt = '2026-10-10T00:00:00Z') {
  return [
    {
      citationId: 'c1',
      citationData: {
        _id: 'c1',
        content: 'x',
        chunkIndex: 1,
        citationType: 'vectordb|document',
        metadata: { ...metadata, ...meta },
        createdAt: answerSavedAt,
        updatedAt: answerSavedAt,
      },
    },
  ] as unknown as CitationApiResponse[];
}

describe('citation source label', () => {
  it('names the connector instance and labels actions with the type', () => {
    const config = getConnectorConfig('SHAREPOINT ONLINE', 'Marketing SharePoint');

    expect(config.sourceLabel).toBe('Marketing SharePoint');
    expect(config.label).toBe('Sharepoint Online');
  });

  it('shows a readable type, not the raw code, for answers saved without names', () => {
    const config = getConnectorConfig('SHAREPOINT ONLINE');

    expect(config.label).toBe('Sharepoint Online');
    expect(config.sourceLabel).toBe('Sharepoint Online');
  });

  it('keeps Collections as the source for uploaded files', () => {
    const config = getConnectorConfig('KB', 'Knowledge Base');

    expect(config.sourceLabel).toBe(i18n.t('nav.collections'));
  });

  it('names the collection a citation came from, as it names a connector', () => {
    const config = getConnectorConfig('KB', 'Q3 Finance');

    expect(config.sourceLabel).toBe('Q3 Finance');
    expect(config.label).toBe(i18n.t('nav.collections'));
  });

  it('keeps Collections for a collection citation saved without a name', () => {
    expect(getConnectorConfig('KB').sourceLabel).toBe(i18n.t('nav.collections'));
    expect(getConnectorConfig('KB', '  ').sourceLabel).toBe(i18n.t('nav.collections'));
  });

  it('keeps the instance name from a saved answer and while streaming', () => {
    const names = { connectorInstanceName: 'Marketing SharePoint' };

    const fromApi = buildCitationMapsFromApi(saved(names)).citations.c1;
    const streamed = buildCitationMapsFromStreaming([
      {
        content: 'x',
        chunkIndex: 1,
        citationType: 'vectordb|document',
        metadata: { ...metadata, ...names, origin: 'CONNECTOR', blockText: '', blockType: 'text', hideWeburl: false },
      },
    ] as unknown as StreamingCitationData[]).citations['streaming-1'];

    for (const c of [fromApi, streamed]) {
      expect(c.connectorInstanceName).toBe('Marketing SharePoint');
    }
  });
});

describe('citation sync time', () => {
  it('is the record update time, not when the answer was saved', () => {
    const c = buildCitationMapsFromApi(saved({ updatedAt: '2026-09-01T00:00:00Z' })).citations.c1;

    expect(c.updatedAt).toBe('2026-09-01T00:00:00Z');
  });

  it('is unknown on answers saved without it, instead of the answer time', () => {
    expect(buildCitationMapsFromApi(saved({})).citations.c1.updatedAt).toBeUndefined();
  });

  it('is shown while an answer streams', () => {
    const streamed = buildCitationMapsFromStreaming([
      {
        content: 'x',
        chunkIndex: 1,
        citationType: 'vectordb|document',
        metadata: {
          ...metadata,
          origin: 'CONNECTOR',
          blockText: '',
          blockType: 'text',
          hideWeburl: false,
          updatedAt: '2026-09-01T00:00:00Z',
        },
      },
    ] as unknown as StreamingCitationData[]).citations['streaming-1'];

    expect(streamed.updatedAt).toBe('2026-09-01T00:00:00Z');
  });
});
