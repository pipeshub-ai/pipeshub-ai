/**
 * WP96-UI (ADR-RUN-01 D3): a file of a repository the run fetched is a source
 * in the chat's list, named by its plain label ("onyx-dot-app/onyx ·
 * backend/onyx/main.py") under a localized "Repository", and opens the host's
 * page for the file at the fetched commit.
 */
import { describe, it, expect, vi } from 'vitest';
import type { CitationApiResponse } from '@/chat/types';
import { buildCitationMapsFromApi, getConnectorConfig } from '../utils';
import { openRecordSource } from '@/app/(main)/chat/utils/open-record-source';
import { i18n } from '@/lib/i18n';

const BLOB =
  'https://github.com/onyx-dot-app/onyx/blob/4f83564c1f0e8596be3f71d78f847a96447a3bdd/backend/onyx/main.py';

const citation = [
  {
    citationId: 'c1',
    citationData: {
      _id: 'c1',
      content: 'onyx-dot-app/onyx · backend/onyx/main.py at commit 4f83564c1f0e',
      chunkIndex: 1,
      citationType: 'web|repository',
      metadata: {
        recordId: BLOB,
        recordName: 'onyx-dot-app/onyx · backend/onyx/main.py',
        webUrl: BLOB,
        origin: 'REPOSITORY',
        connector: 'REPOSITORY',
        recordType: 'WEBPAGE',
        mimeType: 'text/plain',
        sourceKind: 'web',
        orgId: 'o1',
      },
    },
  },
] as unknown as CitationApiResponse[];

describe('repository sources', () => {
  it('lists the file under its plain label', () => {
    const maps = buildCitationMapsFromApi(citation);
    const cited = maps.citations['c1'];
    expect(cited?.recordName).toBe('onyx-dot-app/onyx · backend/onyx/main.py');
    expect(cited?.connector).toBe('REPOSITORY');
    expect(Object.keys(maps.sources)).toEqual([BLOB]);
  });

  it('names the source kind in the reader language', () => {
    expect(getConnectorConfig('REPOSITORY').label).toBe(i18n.t('connectorTypes.repository'));
    expect(i18n.t('connectorTypes.repository')).not.toBe('connectorTypes.repository');
  });

  it("opens the host's page for the file at the commit", async () => {
    const openWindow = vi.fn();
    const opened = await openRecordSource(
      { recordId: BLOB, connector: 'REPOSITORY', webUrl: BLOB },
      { openWindow },
    );
    expect(opened).toEqual({ opened: 'web', url: BLOB });
    expect(openWindow).toHaveBeenCalledWith(BLOB);
  });
});
