/**
 * Mongo implementation of the conversation repository. The queries are the
 * ones the controllers used to run, moved here unchanged (same filters,
 * projections, sorts, lean/populate, options, call order). Ids come in as
 * strings and go out as strings; Mongoose casts them the way it always did.
 */
import { randomUUID } from 'crypto';
import mongoose from 'mongoose';
import { ChatSession } from '../../../modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../modules/enterprise_search/schema/citation.schema';
import { CONVERSATION_STATUS } from '../../../modules/enterprise_search/constants/constants';
import { ConflictError, NotFoundError } from '../errors';
import { InternalServerError } from '../../errors/http.errors';
import { Logger } from '../../services/logger.service';
import { toPlain } from '../plain';
import { sessionOf } from '../mongo';
import { asQueryFilter } from '../../utils/mongo-filter';
import type {
  IChatAttachmentRef,
  IChatSessionDocument,
} from '../../../modules/enterprise_search/types/conversation.interfaces';
import type { Tx } from '../uow';
import { withTiebreak } from '../sort';
import type {
  AgentArchiveGroup,
  ArchivedSearchResult,
  AttachmentsAndCitedRecords,
  ChatSessionDto,
  CitationDto,
  ConversationRepository,
  MessageDto,
  MessageRow,
  NewCitation,
  NewMessage,
  NewSession,
  ContentMatches,
  VisibleSummaryRow,
  RunReservation,
  RunStateChange,
  SessionActor,
  SessionListQuery,
  SessionPage,
  SessionScope,
  SessionShareDto,
  SessionSummary,
} from './types';

const logger = new Logger({ service: 'conversation-repository' });

const oid = (id: string): mongoose.Types.ObjectId =>
  new mongoose.Types.ObjectId(id);
const oids = (ids: string[]): mongoose.Types.ObjectId[] => ids.map(oid);
const opts = (tx?: Tx): { session: mongoose.ClientSession } | undefined => {
  const session = sessionOf(tx);
  return session ? { session } : undefined;
};

/** `sessionType` (and the agent key when named) for a scope. */
const scopeFilter = (scope: SessionScope): Record<string, unknown> =>
  scope.kind === 'chat'
    ? { sessionType: 'chat' }
    : {
        sessionType: 'agent',
        ...(scope.agentKey ? { agentKey: scope.agentKey } : {}),
      };

/** Only the session type, for updates that address a row by id. */
const kindFilter = (scope: SessionScope): Record<string, unknown> => ({
  sessionType: scope.kind === 'chat' ? 'chat' : 'agent',
});

/** Who may delete or archive a chat: the person who started it, or a member it was shared with for writing. */
const initiatorOrWriter = (userId: string): Record<string, unknown>[] => [
  { initiator: userId },
  { 'sharedWith.userId': userId, 'sharedWith.accessLevel': 'write' },
];

/** The owner. A recipient of a shared agent chat can read it but never continue, cancel or regenerate it. */
const ownerOrWriter = (
  _scope: SessionScope,
  userId: string,
): Record<string, unknown> => ({ userId });

/** The Mongo filter for a typed session list query (what `buildFilter` and `buildAgentConversationFilter` built). */
export function buildSessionFilter(
  q: SessionListQuery,
): Record<string, unknown> {
  const userOid = oid(q.userId);
  const accessible =
    q.accessibleProjectIds && q.accessibleProjectIds.length > 0
      ? oids(q.accessibleProjectIds)
      : [];
  const filter: Record<string, unknown> =
    q.scope.kind === 'chat'
      ? {
          orgId: oid(q.orgId),
          isDeleted: false,
          $or: [
            ...(q.owned ? [{ userId: userOid }] : []),
            ...(q.shared
              ? [
                  {
                    $and: [
                      { isShared: true },
                      { 'sharedWith.userId': userOid },
                    ],
                  },
                ]
              : []),
            // A chat shared to its project is visible to every member who may view the project.
            ...(q.shared && accessible.length > 0
              ? [
                  {
                    projectId: { $in: accessible },
                    projectVisibility: 'project',
                  },
                ]
              : []),
          ],
        }
      : {
          ...scopeFilter(q.scope),
          orgId: oid(q.orgId),
          $or: [
            { userId: userOid },
            ...(q.sharedAccess
              ? [
                  {
                    isShared: true,
                    // The owner archiving a chat takes it away from the people it was shared with.
                    isArchived: { $ne: true },
                    sharedWith: {
                      $elemMatch: {
                        userId: userOid,
                        ...(q.sharedAccess === 'write' && {
                          accessLevel: 'write',
                        }),
                      },
                    },
                  },
                ]
              : []),
            ...(accessible.length > 0
              ? [
                  {
                    projectId: { $in: accessible },
                    projectVisibility: 'project',
                    isArchived: { $ne: true },
                  },
                ]
              : []),
          ],
          isDeleted: false,
        };
  if (q.scope.kind === 'chat') Object.assign(filter, { isArchived: false });
  if (q.id) filter._id = oid(q.id);
  if (q.projectFilter?.kind === 'unassigned')
    filter.projectId = { $exists: false };
  else if (q.projectFilter) filter.projectId = oid(q.projectFilter.id);
  if (q.search !== undefined) {
    filter.$and = [
      {
        $or: [
          { title: { $regex: q.search, $options: 'i' } },
          ...(q.contentMatchIds && q.contentMatchIds.length > 0
            ? [{ _id: { $in: oids(q.contentMatchIds) } }]
            : []),
        ],
      },
    ];
  }
  if (q.startDate || q.endDate) {
    const createdAt: Record<string, Date> = {};
    if (q.startDate) createdAt.$gte = q.startDate;
    if (q.endDate) createdAt.$lte = q.endDate;
    filter.createdAt = createdAt;
  }
  if (q.isSharedFilter !== undefined) filter.isShared = q.isSharedFilter;
  if (q.archived === 'notTrue') filter.isArchived = { $ne: true };
  else if (q.archived === 'only')
    Object.assign(filter, { isArchived: true, archivedBy: { $exists: true } });
  const scoped = { ...filter, ...scopeFilter(q.scope) };
  if (
    q.scope.kind === 'chat' &&
    q.shared &&
    !q.owned &&
    q.sharedIncludesAgentChats
  )
    scoped.sessionType = { $in: ['chat', 'agent'] };
  return scoped;
}

/** Agent chats shared with the user (not archived); a status can narrow them. */
export function buildAgentSharedWithMeFilter(q: {
  orgId: string;
  userId: string;
  agentKey: string;
  status?: string;
}): Record<string, unknown> {
  const filter: Record<string, unknown> = {
    sessionType: 'agent',
    agentKey: q.agentKey,
    orgId: oid(q.orgId),
    isDeleted: false,
    isShared: true,
    'sharedWith.userId': q.userId,
  };
  if (q.status) filter.status = q.status;
  filter.isArchived = { $ne: true };
  return filter;
}

const ACTIVE_RUN_WINDOW_MS = 10 * 60 * 1000;
const ACTIVE_RUN_SETTLE_MS = 15_000;
const ACTIVE_RUN_POLL_MS = 250;

const AI_MODEL_FIELDS = [
  'modelKey',
  'modelName',
  'modelProvider',
  'chatMode',
  'modelFriendlyName',
  'reasoningEffort',
] as const;

const SUMMARY_FIELDS = {
  orgId: 1,
  title: 1,
  initiator: 1,
  createdAt: 1,
  isShared: 1,
  sharedWith: 1,
  status: 1,
  failReason: 1,
  modelInfo: 1,
  projectId: 1,
  projectVisibility: 1,
};

const msgOf = (doc: unknown): MessageDto => toPlain<MessageDto>(doc);

const toRow = (dto: MessageDto): MessageRow => ({ dto });

export class MongoConversationRepository implements ConversationRepository {
  isConnected(): boolean {
    return (
      (mongoose.connection as mongoose.Connection | undefined)?.readyState ===
      mongoose.ConnectionStates.connected
    );
  }

  async findById(id: string): Promise<ChatSessionDto | null> {
    const doc = await ChatSession.findById(id);
    return doc ? toPlain<ChatSessionDto>(doc) : null;
  }

  // ---- sessions: create and read
  async openSession(
    fields: NewSession,
    firstMessage: NewMessage,
    tx?: Tx,
  ): Promise<ChatSessionDto> {
    const saved = await this.createSession(fields, tx);
    await this.appendMessages(saved._id, fields.orgId, [firstMessage], tx);
    return saved;
  }

  async createSession(fields: NewSession, tx?: Tx): Promise<ChatSessionDto> {
    const { id, metadata, ...rest } = fields;
    const saved = await new ChatSession({
      ...rest,
      ...(id && { _id: id }),
      ...(metadata && { metadata: new Map(Object.entries(metadata)) }),
    }).save(opts(tx));
    return toPlain<ChatSessionDto>(saved);
  }

  async findForTurn(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const doc = await ChatSession.findOne(
      {
        _id: id,
        orgId: actor.orgId,
        isDeleted: false,
        ...scopeFilter(scope),
        ...ownerOrWriter(scope, actor.userId),
      },
      null,
      opts(tx),
    );
    return doc ? toPlain<ChatSessionDto>(doc) : null;
  }

  async findAgentWithAccess(
    id: string,
    agentKey: string,
    actor: SessionActor,
    level: 'read' | 'write',
  ): Promise<ChatSessionDto | null> {
    try {
      const doc = await ChatSession.findOne({
        sessionType: 'agent',
        _id: id,
        agentKey,
        orgId: actor.orgId,
        isDeleted: false,
        $or: [
          { userId: actor.userId },
          {
            isShared: true,
            isArchived: { $ne: true },
            // One entry must carry both the user and the level (separate dotted paths match across entries).
            sharedWith: {
              $elemMatch: {
                userId: actor.userId,
                ...(level === 'write' && { accessLevel: 'write' }),
              },
            },
          },
        ],
      });
      return doc ? toPlain<ChatSessionDto>(doc) : null;
    } catch (error: unknown) {
      logger.error('Error validating agent conversation access', {
        conversationId: id,
        agentKey,
        userId: actor.userId,
        accessLevel: level,
        error: error instanceof Error ? error.message : String(error),
      });
      // A malformed id can match nothing; any other failure is not "not found".
      if (error instanceof mongoose.Error.CastError) return null;
      throw error;
    }
  }

  async findOwnedByInitiator(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
  ): Promise<ChatSessionDto | null> {
    const doc = await ChatSession.findOne({
      _id: id,
      orgId: actor.orgId,
      userId: actor.userId,
      initiator: actor.userId,
      isDeleted: false,
      ...scopeFilter(scope),
    });
    return doc ? toPlain<ChatSessionDto>(doc) : null;
  }

  async findOwned(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const doc = await ChatSession.findOne(
      {
        _id: id,
        orgId: actor.orgId,
        userId: actor.userId,
        isDeleted: false,
        ...scopeFilter(scope),
      },
      null,
      tx ? opts(tx) : {},
    );
    return doc ? toPlain<ChatSessionDto>(doc) : null;
  }

  async findForShare(
    id: string,
    actor: SessionActor,
    scope: SessionScope = { kind: 'chat' },
  ): Promise<ChatSessionDto | null> {
    const doc = await ChatSession.findOne({
      _id: id,
      orgId: actor.orgId,
      userId: actor.userId,
      isDeleted: false,
      initiator: actor.userId, // Only initiator can share
      ...scopeFilter(scope),
    });
    return doc ? toPlain<ChatSessionDto>(doc) : null;
  }

  async findForUnshare(
    id: string,
    actor: SessionActor,
    scope: SessionScope = { kind: 'chat' },
  ): Promise<ChatSessionDto | null> {
    const doc = await ChatSession.findOne({
      _id: id,
      orgId: actor.orgId,
      isDeleted: false,
      initiator: actor.userId, // Only initiator can unshare
      ...scopeFilter(scope),
    });
    return doc ? toPlain<ChatSessionDto>(doc) : null;
  }

  async findDeletable(
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const doc = await ChatSession.findOne(
      {
        _id: id,
        userId: actor.userId,
        orgId: actor.orgId,
        isDeleted: false,
        sessionType: 'chat',
        $or: initiatorOrWriter(actor.userId),
      },
      undefined,
      tx ? { session: sessionOf(tx) } : undefined,
    );
    return doc ? toPlain<ChatSessionDto>(doc) : null;
  }

  async findArchivable(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    if (scope.kind === 'chat') {
      // Read outside the transaction, as before.
      const doc = await ChatSession.findOne({
        _id: id,
        userId: actor.userId,
        orgId: actor.orgId,
        isDeleted: false,
        sessionType: 'chat',
        $or: initiatorOrWriter(actor.userId),
      });
      return doc ? toPlain<ChatSessionDto>(doc) : null;
    }
    const doc = await ChatSession.findOne(
      {
        _id: id,
        userId: actor.userId,
        orgId: actor.orgId,
        agentKey: scope.agentKey,
        isDeleted: false,
        sessionType: 'agent',
      },
      null,
      { session: sessionOf(tx) },
    );
    return doc ? toPlain<ChatSessionDto>(doc) : null;
  }

  async findForFeedback(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const query =
      scope.kind === 'chat'
        ? {
            _id: id,
            orgId: actor.orgId,
            isDeleted: false,
            sessionType: 'chat',
            $or: [
              { initiator: actor.userId },
              { isShared: true, 'sharedWith.userId': actor.userId },
            ],
          }
        : {
            _id: id,
            orgId: actor.orgId,
            userId: actor.userId,
            agentKey: scope.agentKey,
            isDeleted: false,
            sessionType: 'agent',
          };
    const doc = await ChatSession.findOne(
      asQueryFilter<IChatSessionDocument>(query),
      null,
      opts(tx),
    );
    return doc ? toPlain<ChatSessionDto>(doc) : null;
  }

  async findVisibleSummary(
    q: SessionListQuery,
  ): Promise<SessionSummary | null> {
    const row = await ChatSession.findOne(buildSessionFilter(q))
      .select(SUMMARY_FIELDS)
      .lean()
      .exec();
    return row ? toPlain<SessionSummary>(row) : null;
  }

  async listSessions(q: SessionListQuery): Promise<SessionPage> {
    const filter = buildSessionFilter(q);
    let selection = ChatSession.find(filter)
      .sort(withTiebreak(q.sort))
      .skip(q.skip)
      .limit(q.limit)
      .select('-__v');
    if (q.omitSharedWith)
      selection = selection.select('-sharedWith -isPinned') as typeof selection;
    const [rows, total] = await Promise.all([
      selection.lean().exec(),
      ChatSession.countDocuments(asQueryFilter<IChatSessionDocument>(filter)),
    ]);
    return { rows: toPlain<ChatSessionDto[]>(rows), total };
  }

  async listAgentSharedWithMe(q: {
    orgId: string;
    userId: string;
    agentKey: string;
    status?: string;
    sort: Record<string, 1 | -1>;
    skip: number;
    limit: number;
  }): Promise<ChatSessionDto[]> {
    const filter = buildAgentSharedWithMeFilter(q);
    const rows = await ChatSession.find(filter)
      .sort(withTiebreak(q.sort))
      .skip(q.skip)
      .limit(q.limit)
      .select('-__v')
      .select('-sharedWith')
      .lean()
      .exec();
    return toPlain<ChatSessionDto[]>(rows);
  }

  async searchArchived(q: {
    orgId: string;
    userId: string;
    escapedSearch: string;
    contentMatchIds: string[];
    deletedAgentKeys: string[] | null;
    skip: number;
    limit: number;
  }): Promise<ArchivedSearchResult> {
    const orgOid = oid(q.orgId);
    const userOid = oid(q.userId);
    const titleOrContentMatch = {
      $or: [
        { title: { $regex: q.escapedSearch, $options: 'i' } },
        ...(q.contentMatchIds.length > 0
          ? [{ _id: { $in: oids(q.contentMatchIds) } }]
          : []),
      ],
    };
    const assistantAccessPredicate: Record<string, unknown> = {
      sessionType: 'chat',
      $or: [
        { userId: userOid },
        { $and: [{ 'sharedWith.userId': userOid }, { isShared: true }] },
      ],
    };
    const agentAccessPredicate: Record<string, unknown> = {
      sessionType: 'agent',
      userId: userOid,
    };
    if (q.deletedAgentKeys !== null)
      agentAccessPredicate.agentKey = { $nin: q.deletedAgentKeys };
    const baseFilter = {
      orgId: orgOid,
      isDeleted: false,
      isArchived: true,
      archivedBy: { $exists: true },
      $and: [titleOrContentMatch],
    };
    const [rows, assistantCount, agentCount] = await Promise.all([
      ChatSession.aggregate([
        {
          $match: {
            ...baseFilter,
            $or: [assistantAccessPredicate, agentAccessPredicate],
          },
        },
        {
          $addFields: {
            source: {
              $cond: [{ $eq: ['$sessionType', 'agent'] }, 'agent', 'assistant'],
            },
          },
        },
        { $sort: { lastActivityAt: -1, _id: -1 } },
        { $skip: q.skip },
        { $limit: q.limit },
        { $project: { __v: 0, nextSeq: 0, sessionType: 0 } },
      ]),
      ChatSession.countDocuments({
        ...baseFilter,
        ...assistantAccessPredicate,
      }),
      ChatSession.countDocuments({ ...baseFilter, ...agentAccessPredicate }),
    ]);
    return { rows: toPlain(rows), assistantCount, agentCount };
  }

  async listArchivedAgentGroups(q: {
    orgId: string;
    userId: string;
    deletedAgentKeys: string[] | null;
    agentSkip: number;
    agentLimit: number;
    chatsPerAgent: number;
  }): Promise<{ groups: AgentArchiveGroup[]; totalAgents: number }> {
    const filter: Record<string, unknown> = {
      orgId: oid(q.orgId),
      $or: [{ userId: oid(q.userId) }],
      isArchived: true,
      archivedBy: { $exists: true },
      isDeleted: false,
      sessionType: 'agent',
    };
    if (q.deletedAgentKeys !== null)
      filter.agentKey = { $nin: q.deletedAgentKeys };
    const [countRows, rawGroups] = await Promise.all([
      ChatSession.aggregate<{ totalAgentCount: number }>([
        { $match: filter },
        { $group: { _id: '$agentKey' } },
        { $count: 'totalAgentCount' },
      ]),
      ChatSession.aggregate([
        { $match: filter },
        { $sort: { lastActivityAt: -1 as const, _id: -1 as const } },
        { $project: { __v: 0, messages: 0, nextSeq: 0, sessionType: 0 } },
        {
          $group: {
            _id: '$agentKey',
            conversations: { $push: '$$ROOT' },
            totalCount: { $sum: 1 },
            latestActivity: { $first: '$lastActivityAt' },
          },
        },
        { $sort: { latestActivity: -1 as const, _id: 1 as const } },
        { $skip: q.agentSkip },
        { $limit: q.agentLimit },
        {
          $project: {
            agentKey: '$_id',
            conversations: { $slice: ['$conversations', q.chatsPerAgent] },
            totalCount: 1,
          },
        },
      ]),
    ]);
    return {
      groups: toPlain<AgentArchiveGroup[]>(rawGroups),
      totalAgents: countRows[0]?.totalAgentCount ?? 0,
    };
  }

  async findIdsByContent(
    orgId: string,
    escapedSearch: string,
    limit = 10000,
  ): Promise<ContentMatches> {
    const rows = await ChatSessionMessage.aggregate<{
      _id: mongoose.Types.ObjectId;
    }>([
      {
        $match: {
          orgId: oid(orgId),
          content: { $regex: escapedSearch, $options: 'i' },
        },
      },
      { $group: { _id: '$sessionId' } },
      // One more than asked for tells whether the search was cut short.
      { $limit: limit + 1 },
    ]);
    return {
      ids: rows.slice(0, limit).map((r) => String(r._id)),
      truncated: rows.length > limit,
    };
  }

  async listVisibleSummaries(
    orgId: string,
    userId: string,
    ids: string[],
    accessibleProjectIds: string[],
  ): Promise<VisibleSummaryRow[]> {
    const userObjectId = oid(userId);
    const sessions = await ChatSession.find(
      {
        _id: { $in: oids(ids) },
        orgId: oid(orgId),
        isDeleted: { $ne: true },
        $or: [
          { userId: userObjectId },
          {
            $and: [
              { isShared: true },
              { 'sharedWith.userId': userObjectId },
              // Archiving an agent chat takes it away from the people it was shared with.
              {
                $or: [
                  { sessionType: { $ne: 'agent' } },
                  { isArchived: { $ne: true } },
                ],
              },
            ],
          },
          ...(accessibleProjectIds.length > 0
            ? [
                {
                  projectId: { $in: oids(accessibleProjectIds) },
                  projectVisibility: 'project' as const,
                  // Project members cannot open an archived chat.
                  isArchived: { $ne: true },
                },
              ]
            : []),
        ],
      },
      {
        _id: 1,
        title: 1,
        sessionType: 1,
        agentKey: 1,
        projectId: 1,
        isArchived: 1,
      },
    ).lean();
    return toPlain<VisibleSummaryRow[]>(sessions);
  }

  async listByProject(q: {
    orgId: string;
    projectId: string;
    userId: string;
    skip: number;
    limit: number;
  }): Promise<SessionPage> {
    const filter = {
      orgId: oid(q.orgId),
      projectId: oid(q.projectId),
      isDeleted: false,
      $or: [{ userId: oid(q.userId) }, { projectVisibility: 'project' }],
    };
    const [rows, total] = await Promise.all([
      ChatSession.find(asQueryFilter<IChatSessionDocument>(filter))
        .sort({ lastActivityAt: -1, _id: -1 })
        .skip(q.skip)
        .limit(q.limit)
        .select('-__v')
        .lean()
        .exec(),
      ChatSession.countDocuments(asQueryFilter<IChatSessionDocument>(filter)),
    ]);
    return { rows: toPlain<ChatSessionDto[]>(rows), total };
  }

  async countByProject(
    orgId: string,
    userId: string,
    projectIds: string[],
  ): Promise<Map<string, number>> {
    if (projectIds.length === 0) return new Map();
    const counts = await ChatSession.aggregate<{
      _id: mongoose.Types.ObjectId;
      count: number;
    }>([
      {
        $match: {
          orgId: oid(orgId),
          projectId: { $in: oids(projectIds) },
          isDeleted: false,
          $or: [{ userId: oid(userId) }, { projectVisibility: 'project' }],
        },
      },
      { $group: { _id: '$projectId', count: { $sum: 1 } } },
    ]);
    return new Map(counts.map((c) => [c._id.toString(), c.count]));
  }

  async unlinkProject(projectId: string, tx?: Tx): Promise<void> {
    await ChatSession.updateMany(
      { projectId: oid(projectId) },
      { $unset: { projectId: '', projectVisibility: '' } },
      opts(tx),
    );
  }

  async unlinkFromProject(
    orgId: string,
    sessionId: string,
    projectId: string,
  ): Promise<void> {
    await ChatSession.updateOne(
      { _id: oid(sessionId), orgId: oid(orgId), projectId: oid(projectId) },
      { $unset: { projectId: '', projectVisibility: '' } },
    );
  }

  // ---- sessions: change
  async recordRunState(
    id: string,
    change: RunStateChange,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const conversation = await ChatSession.findOne({ _id: id }, null, opts(tx));
    if (!conversation) return null;
    if (change.status !== undefined) conversation.status = change.status;
    if (change.failReason === null) conversation.failReason = undefined;
    else if (change.failReason !== undefined)
      conversation.failReason = change.failReason;
    if (change.modelInfo) {
      for (const field of AI_MODEL_FIELDS) {
        const value: unknown = change.modelInfo[field];
        if (value !== undefined && value !== null) {
          (conversation.modelInfo as unknown as Record<string, unknown>)[
            field
          ] = value;
        }
      }
    }
    if (change.lastActivityAt !== undefined)
      conversation.lastActivityAt = change.lastActivityAt;
    if (change.title !== undefined) conversation.title = change.title;
    if (change.appendError) {
      if (!conversation.conversationErrors)
        conversation.conversationErrors = [];
      conversation.conversationErrors.push({
        message: change.appendError.message,
        errorType: change.appendError.errorType,
        timestamp: new Date(),
        messageId: change.appendError.messageId as never,
        stack: change.appendError.stack,
        metadata: change.appendError.metadata,
      });
    }
    const saved = await conversation.save(opts(tx));
    return toPlain<ChatSessionDto>(saved);
  }

  async setTitle(
    id: string,
    title: string,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const conv = await ChatSession.findOne(
      { _id: id },
      null,
      tx ? opts(tx) : {},
    );
    if (!conv) return null;
    conv.title = title;
    await conv.save(tx ? (opts(tx) ?? {}) : {});
    return toPlain<ChatSessionDto>(conv);
  }

  async setSharing(
    scope: SessionScope,
    id: string,
    patch: {
      isShared?: boolean;
      sharedWith: SessionShareDto[];
      clearShareLink?: boolean;
    },
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const updateObject: Record<string, unknown> = {};
    if (patch.isShared !== undefined) updateObject.isShared = patch.isShared;
    updateObject.sharedWith = patch.sharedWith;
    // An undefined value is dropped from an update, so the link is unset explicitly.
    if (patch.clearShareLink) updateObject.$unset = { shareLink: 1 };
    const updated = await ChatSession.findOneAndUpdate(
      { _id: id, ...kindFilter(scope) },
      updateObject,
      {
        returnDocument: 'after',
        session: sessionOf(tx),
        runValidators: true,
      },
    );
    return updated ? toPlain<ChatSessionDto>(updated) : null;
  }

  async setArchived(
    scope: SessionScope,
    id: string,
    archived: { by: string } | null,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const set = archived
      ? {
          isArchived: true,
          archivedBy: archived.by,
          lastActivityAt: Date.now(),
        }
      : { isArchived: false, archivedBy: null, lastActivityAt: Date.now() };
    const updated = await ChatSession.findOneAndUpdate(
      { _id: id, ...kindFilter(scope) },
      // An archived chat is out of the list; restoring it must not bring back a pin the owner no longer remembers.
      archived && scope.kind === 'chat'
        ? { $set: set, $unset: { isPinned: 1 } }
        : { $set: set },
      { returnDocument: 'after', session: sessionOf(tx), runValidators: true },
    ).exec();
    return updated ? toPlain<ChatSessionDto>(updated) : null;
  }

  async discardSession(id: string, orgId: string, tx?: Tx): Promise<void> {
    await ChatSessionMessage.deleteMany({ sessionId: id, orgId }, opts(tx));
    await ChatSession.deleteOne({ _id: id, orgId }, opts(tx));
  }

  async setPinned(
    id: string,
    actor: SessionActor,
    pinned: boolean,
    tx?: Tx,
  ): Promise<ChatSessionDto | null> {
    const updated = await ChatSession.findOneAndUpdate(
      {
        _id: id,
        userId: actor.userId,
        orgId: actor.orgId,
        initiator: actor.userId,
        isDeleted: false,
        ...(pinned ? { isArchived: false } : {}),
        ...scopeFilter({ kind: 'chat' }),
      },
      // Unpinning removes the field: a stored `false` would sort ahead of rows without it in a descending sort.
      pinned ? { $set: { isPinned: true } } : { $unset: { isPinned: 1 } },
      // Pinning is not an edit of the chat, so it must not change `updatedAt`.
      { returnDocument: 'after', timestamps: false, session: sessionOf(tx) },
    ).exec();
    return updated ? toPlain<ChatSessionDto>(updated) : null;
  }

  async softDelete(
    id: string,
    actor: SessionActor,
    tx?: Tx,
  ): Promise<{ session: ChatSessionDto; citationIds: string[] } | null> {
    const session = sessionOf(tx);
    const conversation = await this.findDeletable(id, actor, tx);
    if (!conversation) throw new NotFoundError('Conversation not found');

    const updatedConversation = await ChatSession.findOneAndUpdate(
      { _id: id, orgId: actor.orgId, sessionType: 'chat' },
      {
        $set: {
          isDeleted: true,
          deletedBy: actor.userId,
          lastActivityAt: Date.now(),
        },
      },
      { returnDocument: 'after', session, runValidators: true },
    );
    if (!updatedConversation)
      throw new InternalServerError('Failed to delete conversation');

    const sessionMessages = await ChatSessionMessage.find(
      { sessionId: id },
      { citations: 1 },
      { session: session || undefined },
    ).lean();
    const citationIds = sessionMessages
      .filter((msg) => msg.citations?.length)
      .flatMap((msg) => msg.citations?.map((citation) => citation.citationId));

    if (citationIds.length > 0) {
      await Citation.updateMany(
        {
          _id: { $in: citationIds as mongoose.Types.ObjectId[] },
          orgId: actor.orgId,
          isDeleted: false,
        },
        { $set: { isDeleted: true, deletedBy: actor.userId } },
        { session: session || undefined },
      );
    }
    return {
      session: toPlain<ChatSessionDto>(updatedConversation),
      citationIds: citationIds.map(String),
    };
  }

  async softDeleteAgent(
    id: string,
    userId: string,
  ): Promise<ChatSessionDto | null> {
    const conversation = await ChatSession.findOne({ _id: id });
    if (!conversation) return null;
    conversation.isDeleted = true;
    conversation.deletedBy = userId as never;
    conversation.lastActivityAt = Date.now();
    const updated = await conversation.save();
    return toPlain<ChatSessionDto>(updated, { json: true });
  }

  async setProjectLink(
    scope: SessionScope,
    id: string,
    link: {
      projectId: string;
      projectVisibility: 'private' | 'project';
    } | null,
  ): Promise<ChatSessionDto | null> {
    const update = link
      ? {
          $set: {
            projectId: oid(link.projectId),
            projectVisibility: link.projectVisibility,
          },
        }
      : { $unset: { projectId: '', projectVisibility: '' } };
    const updated = await ChatSession.findOneAndUpdate(
      { _id: id, ...scopeFilter(scope) },
      update,
      { returnDocument: 'after' },
    );
    return updated ? toPlain<ChatSessionDto>(updated) : null;
  }

  async setProjectVisibility(
    scope: SessionScope,
    id: string,
    visibility: 'private' | 'project',
  ): Promise<ChatSessionDto | null> {
    const updated = await ChatSession.findOneAndUpdate(
      { _id: id, ...scopeFilter(scope) },
      { $set: { projectVisibility: visibility } },
      { returnDocument: 'after' },
    );
    return updated ? toPlain<ChatSessionDto>(updated) : null;
  }

  // ---- run lease
  /**
   * Claims the thread for a new run in one atomic update, so two requests that
   * arrive together cannot both start one. Reads and writes without a session on
   * purpose: inside a transaction snapshot the status would never change.
   */
  async reserveRun(
    scope: SessionScope,
    id: string,
    actor: SessionActor,
    settleMs: number = ACTIVE_RUN_SETTLE_MS,
  ): Promise<RunReservation | null> {
    const filter = {
      _id: id,
      orgId: actor.orgId,
      isDeleted: false,
      ...scopeFilter(scope),
      ...ownerOrWriter(scope, actor.userId),
    };
    const deadline = Date.now() + settleMs;
    const runId = randomUUID();
    for (;;) {
      const now = Date.now();
      const previous = await ChatSession.findOneAndUpdate(
        {
          $and: [
            filter,
            {
              $or: [
                { status: { $ne: CONVERSATION_STATUS.INPROGRESS } },
                { lastActivityAt: null },
                { lastActivityAt: { $lte: now - ACTIVE_RUN_WINDOW_MS } },
              ],
            },
          ],
        },
        {
          $set: {
            status: CONVERSATION_STATUS.INPROGRESS,
            lastActivityAt: now,
            activeRunId: runId,
          },
        },
        { returnDocument: 'before' },
      )
        .select('status')
        .lean();
      if (previous) {
        const was = String(previous.status ?? CONVERSATION_STATUS.COMPLETE);
        return {
          sessionId: String(previous._id),
          runId,
          previousStatus:
            was === CONVERSATION_STATUS.INPROGRESS
              ? CONVERSATION_STATUS.FAILED
              : was,
        };
      }
      const exists = await ChatSession.findOne(filter).select('_id').lean();
      if (!exists) return null;
      if (Date.now() >= deadline) {
        throw new ConflictError(
          'A response is still being generated for this conversation. Wait for it to finish, or stop it, then send your message again.',
        );
      }
      await new Promise((resolve) => setTimeout(resolve, ACTIVE_RUN_POLL_MS));
    }
  }

  async releaseRun(r: RunReservation): Promise<void> {
    await ChatSession.findOneAndUpdate(
      {
        _id: r.sessionId,
        status: CONVERSATION_STATUS.INPROGRESS,
        activeRunId: r.runId,
      },
      { $set: { status: r.previousStatus } },
    ).lean();
  }

  async touchRun(id: string, runId?: string): Promise<void> {
    await ChatSession.updateOne(
      {
        _id: id,
        status: CONVERSATION_STATUS.INPROGRESS,
        ...(runId ? { activeRunId: runId } : {}),
      },
      { $set: { lastActivityAt: Date.now() } },
      { timestamps: false },
    );
  }

  // ---- messages
  /**
   * Allocates a contiguous block of `seq` numbers with an atomic `$inc` on the
   * session's counter, then inserts. Race-free; the unique `{sessionId, seq}`
   * index is the backstop. `seq` is a sort key only: gaps are legal.
   */
  private async allocateSeq(
    sessionId: string,
    n: number,
    tx?: Tx,
  ): Promise<number> {
    const updated = await ChatSession.findOneAndUpdate(
      { _id: sessionId },
      { $inc: { nextSeq: n } },
      {
        returnDocument: 'after',
        projection: { nextSeq: 1 }, // overrides the schema's `select: false` for this one read
        session: sessionOf(tx),
      },
    );
    if (!updated)
      throw new NotFoundError(
        'Chat session not found while allocating message sequence',
      );
    return updated.nextSeq as number;
  }

  async appendMessages(
    sessionId: string,
    orgId: string,
    messages: NewMessage[],
    tx?: Tx,
  ): Promise<MessageDto[]> {
    if (messages.length === 0) return [];
    const endSeq = await this.allocateSeq(sessionId, messages.length, tx);
    const startSeq = endSeq - messages.length + 1;
    const inserted = await ChatSessionMessage.insertMany(
      messages.map((message, i) => ({
        ...message,
        sessionId,
        orgId,
        seq: startSeq + i,
      })),
      { ordered: true, session: sessionOf(tx) },
    );
    return inserted.map(msgOf);
  }

  /** Wholesale replace, preserving `_id`/`sessionId`/`orgId`/`seq` (regeneration discards the old content). */
  async replaceMessage(
    messageId: string,
    orgId: string,
    newContent: NewMessage,
    tx?: Tx,
  ): Promise<MessageDto | null> {
    const existing = await ChatSessionMessage.findOne(
      { _id: messageId, orgId },
      undefined,
      { session: sessionOf(tx) },
    );
    if (!existing) return null;
    const replaced = await ChatSessionMessage.findOneAndReplace(
      { _id: messageId, orgId },
      {
        ...newContent,
        sessionId: existing.sessionId,
        orgId: existing.orgId,
        seq: existing.seq,
      },
      { returnDocument: 'after', session: sessionOf(tx), runValidators: true },
    );
    return replaced ? msgOf(replaced) : null;
  }

  async deleteMessages(
    messageIds: string[],
    orgId: string,
    tx?: Tx,
    sessionId?: string,
  ): Promise<number> {
    if (messageIds.length === 0) return 0;
    const result = await ChatSessionMessage.deleteMany(
      { _id: { $in: messageIds }, orgId, ...(sessionId ? { sessionId } : {}) },
      opts(tx),
    );
    return result.deletedCount;
  }

  async updateUserQueryContent(
    messageId: string,
    orgId: string,
    sessionId: string,
    content: string,
    attachments?: IChatAttachmentRef[],
    tx?: Tx,
  ): Promise<MessageDto | null> {
    const updated = await ChatSessionMessage.findOneAndUpdate(
      { _id: messageId, orgId, sessionId, messageType: 'user_query' },
      { $set: { content, ...(attachments ? { attachments } : {}) } },
      { returnDocument: 'after', session: sessionOf(tx) },
    );
    return updated ? msgOf(updated) : null;
  }

  async appendFeedback(
    messageId: string,
    orgId: string,
    feedback: unknown,
    tx?: Tx,
  ): Promise<MessageDto | null> {
    const updated = await ChatSessionMessage.findOneAndUpdate(
      { _id: messageId, orgId },
      { $push: { feedback } },
      { returnDocument: 'after', session: sessionOf(tx), runValidators: true },
    );
    return updated ? msgOf(updated) : null;
  }

  async findMessage(
    sessionId: string,
    orgId: string,
    messageId: string,
    tx?: Tx,
  ): Promise<MessageDto | null> {
    const doc = await ChatSessionMessage.findOne(
      { _id: messageId, sessionId, orgId },
      null,
      opts(tx),
    );
    return doc ? msgOf(doc) : null;
  }

  /** `limit <= 0` is an empty page: Mongo's own `.limit(0)` means no limit. */
  async listMessages(
    sessionId: string,
    q: {
      orgId: string;
      skip?: number;
      limit?: number;
      withCitations?: boolean;
      order?: 'asc' | 'desc';
    },
    tx?: Tx,
  ): Promise<MessageRow[]> {
    const { orgId, skip = 0, limit, withCitations = false, order = 'asc' } = q;
    if (limit !== undefined && limit <= 0) return [];
    let query = ChatSessionMessage.find({ sessionId, orgId }).sort({
      seq: order === 'desc' ? -1 : 1,
    });
    if (skip) query = query.skip(skip);
    if (limit !== undefined) query = query.limit(limit);
    if (withCitations) {
      query = query.populate({
        path: 'citations.citationId',
        model: 'citation',
        select: '-__v',
      });
    }
    const session = sessionOf(tx);
    if (session) query = query.session(session);
    const rows = await query.lean().exec();
    return rows.map((row) => toRow(msgOf(row)));
  }

  async listMessagesOfSessions(
    sessionIds: string[],
    q: { orgId: string; withCitations?: boolean },
  ): Promise<Map<string, MessageRow[]>> {
    const out = new Map<string, MessageRow[]>();
    if (sessionIds.length === 0) return out;
    let query = ChatSessionMessage.find({
      sessionId: { $in: sessionIds },
      orgId: q.orgId,
    }).sort({ seq: 1 });
    if (q.withCitations) {
      query = query.populate({
        path: 'citations.citationId',
        model: 'citation',
        select: '-__v',
      });
    }
    for (const row of await query.lean().exec()) {
      const dto = msgOf(row);
      const bucket = out.get(dto.sessionId) ?? [];
      bucket.push(toRow(dto));
      out.set(dto.sessionId, bucket);
    }
    return out;
  }

  async listArtifactMarkerContents(
    sessionId: string,
    orgId: string,
    lastSeq: number,
  ): Promise<string[]> {
    const rows = await ChatSessionMessage.find(
      {
        sessionId,
        orgId,
        seq: { $lte: lastSeq },
        content: { $regex: '::artifact\\[' },
      },
      { content: 1 },
    ).lean();
    return rows.map((row) => {
      const { content } = row as { content?: unknown };
      return typeof content === 'string' ? content : '';
    });
  }

  async countMessages(sessionId: string, orgId: string): Promise<number> {
    return ChatSessionMessage.countDocuments({ sessionId, orgId });
  }

  async listAttachmentsAndCitedRecords(
    sessionId: string,
  ): Promise<AttachmentsAndCitedRecords[]> {
    const rows = await ChatSessionMessage.find(
      { sessionId },
      { attachments: 1, citations: 1 },
    )
      .populate({
        path: 'citations.citationId',
        model: 'citation',
        select: 'metadata.recordId metadata.connector',
      })
      .lean();
    return toPlain<AttachmentsAndCitedRecords[]>(rows);
  }

  // ---- citations
  async createCitations(cs: NewCitation[], tx?: Tx): Promise<CitationDto[]> {
    const session = sessionOf(tx);
    const saved = await Promise.all(
      cs.map((c) => {
        const doc = new Citation(c);
        return session ? doc.save({ session }) : doc.save();
      }),
    );
    return saved.map((doc) => toPlain<CitationDto>(doc, { json: true }));
  }

  async getCitations(ids: string[], orgId: string): Promise<CitationDto[]> {
    if (ids.length === 0) return [];
    const docs = await Citation.find({
      _id: { $in: ids },
      'metadata.orgId': orgId,
    })
      .select('-__v')
      .lean();
    return toPlain<CitationDto[]>(docs);
  }
}
