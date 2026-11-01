import { Decimal128, Long } from 'mongodb';
import { at, oid } from '../roundtrip';

export const ORG1 = oid();
export const ORG2 = oid();
export const U1 = oid();
export const U2 = oid();
export const U3 = oid();
export const S1 = oid();
export const S2 = oid();
export const S3 = oid();
export const S_OTHER = oid();
export const C1 = oid();
export const C2 = oid();
export const C_BAD = oid();
export const SEARCH1 = oid();

const msg = (session: unknown, org: unknown, seq: number, extra: Record<string, unknown>): Record<string, unknown> => ({
  _id: oid(),
  sessionId: session,
  orgId: org,
  seq,
  messageType: 'user_query',
  content: `message ${seq}`,
  contentFormat: 'MARKDOWN',
  citations: [],
  followUpQuestions: [],
  feedback: [],
  modelInfo: { chatMode: 'quick' },
  appliedFilters: { apps: [], kb: [], recordGroups: [], records: [], recordsExact: [] },
  attachments: [],
  referenceData: [],
  tools: [],
  reasoning: [],
  parts: [],
  createdAt: at('2026-03-01T10:00:00.000Z'),
  updatedAt: at('2026-03-01T10:00:00.000Z'),
  ...extra,
});

let cached: Record<string, unknown[]> | undefined;
/** The same documents on every call (fresh ids inside them would make a second copy look different). */
export const conversationDocs = (): Record<string, unknown[]> => (cached ??= build());

const build = (): Record<string, unknown[]> => ({
  chatSessions: [
    {
      _id: S1,
      sessionType: 'chat',
      nextSeq: 3,
      userId: U1,
      orgId: ORG1,
      title: 'Quarterly plan',
      initiator: U1,
      isShared: true,
      isPinned: true,
      shareLink: 'https://x.example/s/1',
      sharedWith: [
        { userId: U2, accessLevel: 'read', _id: oid() },
        { userId: U3, accessLevel: 'write', _id: oid() },
        { userId: U2, accessLevel: 'write', _id: oid() }, // the same user twice: one row, the higher level
      ],
      isDeleted: false,
      isArchived: false,
      lastActivityAt: 1772359200000,
      status: 'Complete',
      modelInfo: { modelKey: 'k1', modelName: 'm', modelProvider: 'p', chatMode: 'quick' },
      conversationErrors: [{ _id: oid(), message: 'boom', errorType: 'x', timestamp: at('2026-03-01T10:01:00.000Z'), metadata: { 'a.b': 'dot key', big: Long.fromString('9007199254740993') } }],
      metadata: { note: 'café', 'k\u0000ey': 'nul\u0000value', dec: Decimal128.fromString('1.10'), old: at('1969-07-20T20:17:00.000Z'), k: 'planted' },
      createdAt: at('2026-03-01T09:59:00.000Z'),
      updatedAt: at('2026-03-01T10:00:00.000Z'),
      __v: 3,
    },
    {
      _id: S2,
      nextSeq: 1,
      userId: U2,
      orgId: ORG1,
      initiator: U2,
      isArchived: true,
      archivedBy: null, // stored as an explicit null
      isDeleted: true,
      deletedBy: U2,
      sessionType: 'agent',
      agentKey: 'agent-1',
      conversationSource: 'agent_chat',
      compactedSummary: 'summary',
      compactedAtTurnIndex: 4,
      compactedAtTimestamp: 1772359200123,
      projectId: oid(),
      projectVisibility: 'project',
      activeRunId: 'run-1',
      lastActivityAt: 1772359300000,
      createdAt: at('2026-03-02T09:59:00.000Z'),
      updatedAt: at('2026-03-02T10:00:00.000Z'),
      __v: 0,
    },
    {
      // sparse: no sessionType, no flags, no model info, no members, no lastActivityAt
      _id: S3,
      nextSeq: 1,
      isPinned: false,
      metadata: { forkedFromConversationId: String(S1), forkedFromMessageId: String(C1) },
      userId: U3,
      orgId: ORG2,
      initiator: U3,
      createdAt: at('2026-03-03T09:59:00.000Z'),
      updatedAt: at('2026-03-03T10:00:00.000Z'),
      __v: 0,
    },
  ],
  chatSessionMessages: [
    msg(S1, ORG1, 0, { authorUserId: U1 }),
    msg(S1, ORG1, 1, {
      messageType: 'bot_response',
      authorUserId: U1,
      content: 'answer with citations',
      confidence: 'High',
      citations: [{ citationId: C1, relevanceScore: 0.9, excerpt: 'x' }, { citationId: C2 }],
      followUpQuestions: [{ question: 'why?', confidence: 'Medium' }],
      feedback: [{ isHelpful: true, ratings: { accuracy: 5 }, feedbackProvider: U2, timestamp: 1772359200000, revisions: [{ updatedFields: ['isHelpful'], previousValues: { isHelpful: false }, updatedAt: 1772359100000 }] }],
      metadata: { processingTimeMs: 12, modelVersion: 'v1', aiTransactionId: 't1' },
      referenceData: [{ name: 'Proj', id: '1', type: 'project', app: 'jira', metadata: { key: 'P-1' } }],
      tools: [{ toolName: 'search', toolResult: { hits: 3, huge: Long.fromString('9007199254740993'), price: Decimal128.fromString('1.10'), 'a.b': 1 } }],
      reasoning: [{ messageId: 'm1', turnIndex: 0, content: 'think' }],
      parts: [{ type: 'text', text: 'hello\u0000there' }, 3],
      sourceRecordIds: ['rec-1', 'rec-2'],
    }),
    // no content format, nothing else (the first messages above carry the author and source-record fields an older release stored)
    (() => {
      const m = msg(S1, ORG1, 2, { messageType: 'user_query' });
      delete m.contentFormat;
      return m;
    })(),
    msg(S2, ORG1, 0, { authorUserId: U2, status: 'stopped', sourceRecordIds: [] }),
    msg(S3, ORG2, 0, { authorUserId: U3 }),
    // belongs to no session in its organisation: not copied
    msg(S_OTHER, ORG1, 0, { authorUserId: U1 }),
  ],
  searches: [
    {
      _id: SEARCH1,
      query: 'budget',
      limit: 10,
      orgId: ORG1,
      userId: U1,
      citationIds: [C1],
      records: { r1: 'Record one' },
      isShared: true,
      sharedWith: [{ userId: U2, accessLevel: 'read', _id: oid() }],
      isArchived: true,
      archivedBy: U1,
      createdAt: at('2026-03-04T10:00:00.000Z'),
      updatedAt: at('2026-03-04T10:00:00.000Z'),
      __v: 1,
    },
    { _id: oid(), query: 'plain', limit: 5, orgId: ORG2, userId: U3, createdAt: at('2026-03-05T10:00:00.000Z'), updatedAt: at('2026-03-05T10:00:00.000Z'), __v: 0 },
  ],
  citations: [
    citation(C1, ORG1, 'rec-1'),
    citation(C2, ORG1, 'rec-2'),
    // no usable organisation: not copied
    { ...citation(C_BAD, ORG1, 'rec-3'), metadata: { recordId: 'rec-3', recordName: 'x', mimeType: 'text/plain', origin: 'UPLOAD', extension: '', webUrl: '', previewRenderable: true, orgId: 'not-an-id' } },
  ],
});

function citation(id: unknown, org: unknown, record: string): Record<string, unknown> {
  return {
    _id: id,
    content: `text of ${record}`,
    chunkIndex: 2,
    citationType: 'vectorDB|document',
    metadata: {
      blockNum: [1, 2],
      pageNum: [3],
      departments: ['eng'],
      orgId: String(org),
      mimeType: 'application/pdf',
      recordId: record,
      recordVersion: 0,
      bounding_box: [{ x: 0.1, y: 0.2 }],
      recordName: `${record}.pdf`,
      origin: 'UPLOAD',
      extension: 'pdf',
      webUrl: 'https://x.example/r',
      previewRenderable: true,
    },
    createdAt: at('2026-03-01T10:00:00.000Z'),
    updatedAt: at('2026-03-01T10:00:00.000Z'),
    __v: 0,
  };
}
