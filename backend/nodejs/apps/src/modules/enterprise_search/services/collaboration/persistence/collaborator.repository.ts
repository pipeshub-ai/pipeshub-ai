import {
  ClientSession,
  FilterQuery,
  PipelineStage,
  QueryOptions,
  Types,
} from 'mongoose';
import { ACL_VERSION_INC } from '../../../../authz/cache/acl-version';
import {
  OWNERSHIP_HISTORY_MAX,
  SHARED_WITH_MAX,
} from '../../../constants/constants';
import { ChatSession } from '../../../schema/chat.session.schema';
import {
  IChatSession,
  IChatSessionDocument,
} from '../../../types/conversation.interfaces';
import { AccessLevel, ConversationSettings, Principal } from '../domain/types';
import { IClock, systemClock } from '../../../../../libs/types/clock';

/** `ownerId` makes the op owner-gated; omit it only for an editor's invite, which the service has already authorised. */
export interface SessionScope {
  sessionId: string;
  orgId: string;
  ownerId?: string;
}

export interface WriteOptions {
  session?: ClientSession | null;
}

export interface WriteStamp {
  rev: number;
  aclVersion: number;
  isShared: boolean;
}

export type AddOutcome =
  | ({ status: 'added' } & WriteStamp)
  | { status: 'already_present' }
  | { status: 'limit'; max: number }
  | { status: 'not_found' };

export type ChangeLevelOutcome =
  | ({ status: 'applied' } & WriteStamp)
  | { status: 'unchanged' }
  | { status: 'absent' }
  | { status: 'not_found' };

export type RemoveOutcome =
  | ({ status: 'applied' } & WriteStamp)
  | { status: 'absent' }
  | { status: 'not_found' };

export type TransferOutcome =
  | ({ status: 'applied' } & WriteStamp)
  | { status: 'no_match' };

export type SettingsOutcome =
  | ({ status: 'applied' } & WriteStamp)
  | { status: 'unchanged' }
  | { status: 'not_found' };

export type LeaveOutcome =
  | ({ status: 'applied' } & WriteStamp)
  | { status: 'owner' }
  | { status: 'not_found' };

export interface AddCollaboratorInput {
  principal: Principal;
  accessLevel: AccessLevel;
  addedBy: string;
}

export interface TransferInput {
  fromUserId: string;
  toUserId: string;
}

export interface ICollaboratorRepository {
  add(
    scope: SessionScope,
    input: AddCollaboratorInput,
    opts?: WriteOptions,
  ): Promise<AddOutcome>;
  changeLevel(
    scope: SessionScope,
    principal: Principal,
    accessLevel: AccessLevel,
    opts?: WriteOptions,
  ): Promise<ChangeLevelOutcome>;
  remove(
    scope: SessionScope,
    principal: Principal,
    opts?: WriteOptions,
  ): Promise<RemoveOutcome>;
  /** Owner-gated on `fromUserId`; `no_match` covers a lost race, a changed owner and a target that is not a direct `write` row. */
  transfer(
    scope: Omit<SessionScope, 'ownerId'>,
    input: TransferInput,
    opts?: WriteOptions,
  ): Promise<TransferOutcome>;
  updateSettings(
    scope: SessionScope,
    patch: ConversationSettings,
    opts?: WriteOptions,
  ): Promise<SettingsOutcome>;
  /** The owner cannot leave: the filter excludes them and the outcome is `owner`. */
  leave(
    scope: Omit<SessionScope, 'ownerId'>,
    userId: string,
    opts?: WriteOptions,
  ): Promise<LeaveOutcome>;
}

const oid = (id: string): Types.ObjectId => new Types.ObjectId(id);

const STAMP_PROJECTION = { rev: 1, aclVersion: 1, isShared: 1 } as const;

/** Pipeline updates take no `$inc`; same effect as `ACL_VERSION_INC` plus `rev`. */
const BUMP_STAGE = {
  rev: { $add: [{ $ifNull: ['$rev', 0] }, 1] },
  aclVersion: { $add: [{ $ifNull: ['$aclVersion', 0] }, 1] },
};

const arr = (field: string): { $ifNull: unknown[] } => ({
  $ifNull: [`$${field}`, []],
});

const rowMatches = (
  p: Principal,
): { userId: Types.ObjectId } | { teamId: string } =>
  p.type === 'user' ? { userId: oid(p.userId) } : { teamId: p.teamId };

const dropRows = (p: Principal): { $filter: Record<string, unknown> } => ({
  $filter: {
    input: arr('sharedWith'),
    cond: {
      $ne: [
        p.type === 'user' ? '$$this.userId' : '$$this.teamId',
        p.type === 'user' ? oid(p.userId) : p.teamId,
      ],
    },
  },
});

const hasRows = { $gt: [{ $size: '$sharedWith' }, 0] };

const toStamp = (
  doc: { rev?: number; aclVersion?: number; isShared?: boolean } | null,
): WriteStamp | null =>
  doc
    ? {
        rev: doc.rev ?? 0,
        aclVersion: doc.aclVersion ?? 0,
        isShared: doc.isShared === true,
      }
    : null;

export class MongoCollaboratorRepository implements ICollaboratorRepository {
  constructor(private readonly clock: IClock = systemClock) {}

  async add(
    scope: SessionScope,
    input: AddCollaboratorInput,
    opts: WriteOptions = {},
  ): Promise<AddOutcome> {
    const { principal } = input;
    const row = {
      principalType: principal.type,
      ...rowMatches(principal),
      accessLevel: input.accessLevel,
      addedBy: oid(input.addedBy),
      addedAt: new Date(this.clock.now()),
    };
    const firstShareOfArchived = {
      $and: [
        { $eq: [{ $size: arr('sharedWith') }, 0] },
        { $eq: ['$isArchived', true] },
      ],
    };
    const archivedBase =
      principal.type === 'user'
        ? { $setDifference: [arr('archivedFor'), [oid(principal.userId)]] }
        : arr('archivedFor');
    const update: PipelineStage.Set[] = [
      {
        $set: {
          sharedWith: {
            $concatArrays: [arr('sharedWith'), [{ $literal: row }]],
          },
          isShared: true,
          ...(principal.type === 'user' && {
            hiddenFor: {
              $setDifference: [arr('hiddenFor'), [oid(principal.userId)]],
            },
          }),
          // Archive before the first share was the owner's global flag; it becomes per-user so a recipient never inherits it.
          archivedFor: {
            $cond: [
              firstShareOfArchived,
              { $setUnion: [archivedBase, ['$userId']] },
              archivedBase,
            ],
          },
          isArchived: {
            $cond: [
              firstShareOfArchived,
              false,
              { $ifNull: ['$isArchived', false] },
            ],
          },
          archivedBy: {
            $cond: [firstShareOfArchived, '$$REMOVE', '$archivedBy'],
          },
          ...BUMP_STAGE,
        },
      },
    ];
    const notPresent =
      principal.type === 'user'
        ? { 'sharedWith.userId': { $ne: oid(principal.userId) } }
        : { 'sharedWith.teamId': { $ne: principal.teamId } };
    const doc = await ChatSession.findOneAndUpdate(
      {
        ...this.filter(scope),
        ...notPresent,
        $expr: { $lt: [{ $size: arr('sharedWith') }, SHARED_WITH_MAX] },
      },
      update,
      this.options(opts),
    )
      .lean()
      .exec();
    const stamp = toStamp(doc);
    if (stamp) {
      return { status: 'added', ...stamp };
    }
    const current = await this.peek(scope, opts);
    if (!current) {
      return { status: 'not_found' };
    }
    return this.hasRow(current.sharedWith, principal)
      ? { status: 'already_present' }
      : { status: 'limit', max: SHARED_WITH_MAX };
  }

  async changeLevel(
    scope: SessionScope,
    principal: Principal,
    accessLevel: AccessLevel,
    opts: WriteOptions = {},
  ): Promise<ChangeLevelOutcome> {
    const idField = principal.type === 'user' ? 'userId' : 'teamId';
    const id =
      principal.type === 'user' ? oid(principal.userId) : principal.teamId;
    const doc = await ChatSession.findOneAndUpdate(
      {
        ...this.filter(scope),
        sharedWith: {
          $elemMatch: { [idField]: id, accessLevel: { $ne: accessLevel } },
        },
      },
      {
        $set: {
          'sharedWith.$[e].accessLevel': accessLevel,
          'sharedWith.$[e].updatedAt': new Date(this.clock.now()),
        },
        $inc: { rev: 1, ...ACL_VERSION_INC.$inc },
      },
      { ...this.options(opts), arrayFilters: [{ [`e.${idField}`]: id }] },
    )
      .lean()
      .exec();
    const stamp = toStamp(doc);
    if (stamp) {
      return { status: 'applied', ...stamp };
    }
    const current = await this.peek(scope, opts);
    if (!current) {
      return { status: 'not_found' };
    }
    return this.hasRow(current.sharedWith, principal)
      ? { status: 'unchanged' }
      : { status: 'absent' };
  }

  async remove(
    scope: SessionScope,
    principal: Principal,
    opts: WriteOptions = {},
  ): Promise<RemoveOutcome> {
    const userSets =
      principal.type === 'user'
        ? {
            hiddenFor: {
              $setDifference: [arr('hiddenFor'), [oid(principal.userId)]],
            },
            archivedFor: {
              $setDifference: [arr('archivedFor'), [oid(principal.userId)]],
            },
          }
        : {};
    const doc = await ChatSession.findOneAndUpdate(
      {
        ...this.filter(scope),
        sharedWith: { $elemMatch: rowMatches(principal) },
      },
      [
        {
          $set: { sharedWith: dropRows(principal), ...userSets, ...BUMP_STAGE },
        },
        { $set: { isShared: hasRows } },
      ],
      this.options(opts),
    )
      .lean()
      .exec();
    const stamp = toStamp(doc);
    if (stamp) {
      return { status: 'applied', ...stamp };
    }
    const current = await this.peek(scope, opts);
    return current ? { status: 'absent' } : { status: 'not_found' };
  }

  async transfer(
    scope: Omit<SessionScope, 'ownerId'>,
    input: TransferInput,
    opts: WriteOptions = {},
  ): Promise<TransferOutcome> {
    const from = oid(input.fromUserId);
    const to = oid(input.toUserId);
    const now = new Date(this.clock.now());
    const doc = await ChatSession.findOneAndUpdate(
      {
        ...this.filter({ ...scope, ownerId: input.fromUserId }),
        sharedWith: { $elemMatch: { userId: to, accessLevel: 'write' } },
      },
      [
        {
          $set: {
            sharedWith: {
              $concatArrays: [
                dropRows({ type: 'user', userId: input.toUserId }),
                [
                  {
                    $literal: {
                      principalType: 'user',
                      userId: from,
                      accessLevel: 'write',
                      addedBy: from,
                      addedAt: now,
                    },
                  },
                ],
              ],
            },
            userId: to,
            initiator: to,
            isShared: true,
            hiddenFor: { $setDifference: [arr('hiddenFor'), [from, to]] },
            ownershipHistory: {
              $slice: [
                {
                  $concatArrays: [
                    arr('ownershipHistory'),
                    [{ $literal: { fromUserId: from, toUserId: to, at: now } }],
                  ],
                },
                -OWNERSHIP_HISTORY_MAX,
              ],
            },
            ...BUMP_STAGE,
          },
        },
      ],
      this.options(opts),
    )
      .lean()
      .exec();
    const stamp = toStamp(doc);
    return stamp ? { status: 'applied', ...stamp } : { status: 'no_match' };
  }

  async updateSettings(
    scope: SessionScope,
    patch: ConversationSettings,
    opts: WriteOptions = {},
  ): Promise<SettingsOutcome> {
    const $set: Record<string, boolean | string> = {};
    if (patch.respondMode !== undefined) {
      $set['settings.respondMode'] = patch.respondMode;
    }
    if (patch.editorsCanInvite !== undefined) {
      $set['settings.editorsCanInvite'] = patch.editorsCanInvite;
    }
    if (patch.ownerContentShared !== undefined) {
      $set['settings.ownerContentShared'] = patch.ownerContentShared;
    }
    if (Object.keys($set).length === 0) {
      return { status: 'unchanged' };
    }
    const doc = await ChatSession.findOneAndUpdate(
      this.filter(scope),
      { $set, $inc: { rev: 1, ...ACL_VERSION_INC.$inc } },
      this.options(opts),
    )
      .lean()
      .exec();
    const stamp = toStamp(doc);
    return stamp ? { status: 'applied', ...stamp } : { status: 'not_found' };
  }

  async leave(
    scope: Omit<SessionScope, 'ownerId'>,
    userId: string,
    opts: WriteOptions = {},
  ): Promise<LeaveOutcome> {
    const me = oid(userId);
    const doc = await ChatSession.findOneAndUpdate(
      { ...this.filter(scope), userId: { $ne: me } },
      [
        {
          $set: {
            sharedWith: dropRows({ type: 'user', userId }),
            hiddenFor: { $setUnion: [arr('hiddenFor'), [me]] },
            ...BUMP_STAGE,
          },
        },
        { $set: { isShared: hasRows } },
      ],
      this.options(opts),
    )
      .lean()
      .exec();
    const stamp = toStamp(doc);
    if (stamp) {
      return { status: 'applied', ...stamp };
    }
    const current = await this.peek(scope, opts);
    return current ? { status: 'owner' } : { status: 'not_found' };
  }

  private filter(scope: SessionScope): FilterQuery<IChatSessionDocument> {
    return {
      _id: oid(scope.sessionId),
      orgId: oid(scope.orgId),
      isDeleted: false,
      ...(scope.ownerId !== undefined && { userId: oid(scope.ownerId) }),
    };
  }

  private options(opts: WriteOptions): QueryOptions<IChatSessionDocument> {
    return {
      new: true,
      projection: STAMP_PROJECTION,
      ...(opts.session && { session: opts.session }),
    };
  }

  private peek(
    scope: SessionScope,
    opts: WriteOptions,
  ): Promise<Pick<IChatSession, 'sharedWith'> | null> {
    const query = ChatSession.findOne(this.filter(scope), {
      sharedWith: 1,
    }).lean();
    return (opts.session ? query.session(opts.session) : query).exec();
  }

  private hasRow(
    rows: IChatSession['sharedWith'] | undefined,
    principal: Principal,
  ): boolean {
    return (rows ?? []).some((r) => {
      const row = r as { userId?: Types.ObjectId; teamId?: string };
      return principal.type === 'user'
        ? row.userId?.toString() === principal.userId
        : row.teamId === principal.teamId;
    });
  }
}
