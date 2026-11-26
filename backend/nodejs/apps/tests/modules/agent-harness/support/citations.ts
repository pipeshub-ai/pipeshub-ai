import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { CitationAccess } from '../../../../src/modules/agent-harness/projection/citation-access';
import { sourceKey } from '../../../../src/modules/agent-harness/projection/citation-source';
import { GoldenJournal } from '../../../contracts/support/durable';

/**
 * Citations whose every identifying field carries a sentinel, so a test can
 * prove none of it reaches a viewer who may not read the record.
 */
export function recordCitation(recordId: string, chunkIndex: number): Record<string, unknown> {
  return {
    content: `SNIPPET-${recordId}`,
    chunkIndex,
    citationType: 'vectordb|document',
    metadata: {
      recordId,
      recordName: `NAME-${recordId}.pdf`,
      webUrl: `https://files.test/URL-${recordId}`,
      orgId: 'org-1',
      mimeType: 'application/pdf',
      origin: 'CONNECTOR',
      connector: 'DRIVE',
    },
  };
}

/**
 * A citation of a file a tool read straight from a connector (C-18): its
 * `recordId` is the temporary record's live id, never a record key.
 */
export function liveCitation(
  externalRecordId: string,
  chunkIndex: number,
  options: { sourceKind?: boolean; connector?: string } = {},
): Record<string, unknown> {
  return {
    content: `SNIPPET-${externalRecordId}`,
    chunkIndex,
    citationType: 'vectordb|document',
    metadata: {
      recordId: `live:TEMP-${externalRecordId}`,
      recordName: `NAME-${externalRecordId}.pdf`,
      webUrl: `https://files.test/URL-${externalRecordId}`,
      orgId: 'org-1',
      mimeType: 'application/pdf',
      origin: 'CONNECTOR',
      connector: options.connector ?? 'DRIVE',
      connectorId: 'toolset-1',
      externalRecordId,
      ...(options.sourceKind === false ? {} : { sourceKind: 'live' }),
    },
  };
}

/** A citation with the metadata of a record in the graph, and `extra` on top. */
export function storedCitation(
  recordId: string,
  chunkIndex: number,
  extra: Record<string, unknown>,
): Record<string, unknown> {
  const citation = recordCitation(recordId, chunkIndex);
  return { ...citation, metadata: { ...(citation['metadata'] as object), ...extra } };
}

export const indexed = (recordId: string) => ({ kind: 'indexed' as const, recordId });

/** A viewer who may read exactly these records. */
export const readableRecords = (...recordIds: string[]): CitationAccess =>
  CitationAccess.from(new Map(recordIds.map((id) => [sourceKey(indexed(id)), 'shown' as const])));

export const live = (externalRecordId: string, connector = 'DRIVE') => ({
  kind: 'live' as const,
  connector,
  connectorId: 'toolset-1',
  externalRecordId,
});

export function webCitation(chunkIndex: number): Record<string, unknown> {
  return {
    content: 'public page text',
    chunkIndex,
    citationType: 'web|url',
    metadata: {
      recordId: 'https://example.test/page',
      recordName: 'https://example.test/page',
      webUrl: 'https://example.test/page',
      origin: 'WEB_SEARCH',
      connector: 'WEB',
    },
  };
}

/** The sentinels a denied `recordId` (or a live item's external id) must never leak through. */
export const leaksOf = (recordId: string): string[] => [
  recordId,
  `SNIPPET-${recordId}`,
  `NAME-${recordId}`,
  `URL-${recordId}`,
];

/** The golden journal with `citations` on its finalized answer (none of the goldens cite). */
export function withCitations(journal: GoldenJournal, citations: unknown[]): GoldenJournal {
  const events = journal.events.map((event): JournalEvent =>
    event.type === 'x.pipeshub.answer.finalized'
      ? { ...event, data: { ...(event.data ?? {}), citations } }
      : event,
  );
  return { ...journal, events };
}

/** A finished conversation the way `HarnessConversationReader` returns it. */
export function finishedConversation(sessionId: string, citations: unknown[]): Record<string, unknown> {
  return {
    _id: sessionId,
    messages: [
      { messageType: 'user_query', content: 'q', citations: [] },
      {
        messageType: 'bot_response',
        content: 'The answer [1] [2].',
        citations: citations.map((citationData, index) => ({
          citationId: `citation-doc-${String(index)}`,
          citationData,
        })),
      },
    ],
  };
}
