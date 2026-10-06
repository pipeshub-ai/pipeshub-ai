import { FilterQuery, Types } from 'mongoose';
import {
  COLLABORATOR_ACCESS_LEVELS,
  EXCLUDE_AGENT,
  ONLY_AGENT,
} from '../../../constants/constants';
import { IChatSession } from '../../../types/conversation.interfaces';
import { Caller, ConversationRef } from '../domain/types';

type SessionFilter = FilterQuery<IChatSession>;
type Clause = Record<string, unknown>;

/** `'any'` spans chat and agent sessions, for surfaces that list both (a project's conversations). */
export type FilterTarget = {
  kind: ConversationRef['kind'] | 'any';
  agentKey?: string;
  /** Narrows to one conversation for by-id flows. */
  conversationId?: string;
};

/** `'exclude'` hides what the caller archived; `'only'` lists it. Applied with the flag on only (the caller decides). */
export type ArchiveScope = 'exclude' | 'only';

export interface ReadFilterOptions {
  /** Projects the caller can see; a chat shared to its project is readable through them. */
  accessibleProjectIds: readonly string[];
  includeOwned: boolean;
  includeShared: boolean;
  /** False drops `sharedWith` rows and keeps project-inherited chats; default true. */
  includeShareRows?: boolean;
  archived?: ArchiveScope;
  /** Drops chats the caller left (`hiddenFor`); lists only, so a team-derived link still opens (72 A.7, LC-21). */
  excludeHidden?: boolean;
}

export interface WriteFilterOptions {
  collab: boolean;
  /** Projects whose members may write to chats shared with the project (ceiling editor and member role editor+). */
  editableProjectIds?: readonly string[];
}

const NO_MATCH: SessionFilter = { $and: [{ _id: { $in: [] } }] };

const oid = (id: string): Types.ObjectId => new Types.ObjectId(id);

function kindClause(target: FilterTarget): Clause {
  if (target.kind === 'any') {
    return {};
  }
  if (target.kind === 'chat') {
    return { ...EXCLUDE_AGENT };
  }
  if (target.agentKey === undefined || target.agentKey === '') {
    throw new Error('An agent filter needs an agentKey');
  }
  return { ...ONLY_AGENT, agentKey: target.agentKey };
}

/**
 * One branch per principal kind, each an `$elemMatch` on its own `sharedWith` index. Matches by id
 * presence, never by `principalType`: a legacy row has no `principalType` (74 §1), and a team branch
 * never matches a row that also names a user. Unresolved teams fail closed to the user branch.
 *
 * Both kinds in one `$elemMatch` with an inner `$or` leaves the planner no usable index: it scans every
 * session of the org and sorts them (PH12-01, 1.4 s at 200k sessions). The level test is left out when
 * `levels` is every level, where it excludes nothing and only adds a residual filter.
 */
export function principalBranches(
  caller: Caller,
  levels: readonly string[],
): Clause[] {
  const teamIds = caller.teamIds === 'unresolved' ? [] : caller.teamIds;
  const everyLevel = COLLABORATOR_ACCESS_LEVELS.every((level) =>
    levels.includes(level),
  );
  const level: Clause = everyLevel ? {} : { accessLevel: { $in: [...levels] } };
  const branches: Clause[] = [
    { sharedWith: { $elemMatch: { userId: oid(caller.userId), ...level } } },
  ];
  if (teamIds.length > 0) {
    branches.push({
      sharedWith: {
        $elemMatch: { userId: null, teamId: { $in: [...teamIds] }, ...level },
      },
    });
  }
  return branches;
}

function accessBranches(
  caller: Caller,
  levels: readonly string[],
  o: {
    owned: boolean;
    shared: boolean;
    shareRows?: boolean;
    projectIds: readonly string[];
  },
): Clause[] {
  const branches: Clause[] = [];
  if (o.owned) {
    branches.push({ userId: oid(caller.userId) });
  }
  if (o.shared) {
    if (o.shareRows !== false) {
      branches.push(...principalBranches(caller, levels));
    }
    if (o.projectIds.length > 0) {
      branches.push({
        projectVisibility: 'project',
        projectId: { $in: o.projectIds.map(oid) },
      });
    }
  }
  return branches;
}

function baseClause(caller: Caller, target: FilterTarget): Clause {
  return {
    orgId: oid(caller.orgId),
    isDeleted: false,
    ...kindClause(target),
    ...(target.conversationId !== undefined && {
      _id: oid(target.conversationId),
    }),
  };
}

/** A shared chat is archived per user (`archivedFor`); an unshared owner chat keeps the global `isArchived`. */
export function archiveClause(caller: Caller, scope: ArchiveScope): Clause {
  const me = oid(caller.userId);
  return scope === 'exclude'
    ? { archivedFor: { $ne: me } }
    : { $or: [{ isArchived: true, userId: me }, { archivedFor: me }] };
}

function listStateClauses(caller: Caller, o: ReadFilterOptions): Clause[] {
  return [
    ...(o.archived ? [archiveClause(caller, o.archived)] : []),
    ...(o.excludeHidden ? [{ hiddenFor: { $ne: oid(caller.userId) } }] : []),
  ];
}

function readBranches(caller: Caller, o: ReadFilterOptions): Clause[] {
  return accessBranches(caller, ['read', 'write'], {
    owned: o.includeOwned,
    shared: o.includeShared,
    shareRows: o.includeShareRows,
    projectIds: o.accessibleProjectIds,
  });
}

/** Always `{ $and: [clause] }` so a caller appending conditions cannot overwrite the access `$or` (F-17). */
export function readFilter(
  caller: Caller,
  target: FilterTarget,
  o: ReadFilterOptions,
): SessionFilter {
  const branches = readBranches(caller, o);
  if (branches.length === 0) {
    return NO_MATCH;
  }
  return {
    $and: [
      { ...baseClause(caller, target), $or: branches },
      ...listStateClauses(caller, o),
    ],
  };
}

/** Same predicate for list endpoints, with the `$or` at the top level so the multikey indexes are usable (71). Do not spread other keys over it. */
export function listFilter(
  caller: Caller,
  target: FilterTarget,
  o: ReadFilterOptions,
): SessionFilter {
  const branches = readBranches(caller, o);
  if (branches.length === 0) {
    return NO_MATCH;
  }
  const state = listStateClauses(caller, o);
  return {
    ...baseClause(caller, target),
    $or: branches,
    ...(state.length > 0 && { $and: state }),
  };
}

/** Owner, or a `write` row; with the flag off a `write` row is read-only, so only the owner matches. */
export function writeFilter(
  caller: Caller,
  target: FilterTarget,
  o: WriteFilterOptions,
): SessionFilter {
  const branches = o.collab
    ? accessBranches(caller, ['write'], {
        owned: true,
        shared: true,
        projectIds: o.editableProjectIds ?? [],
      })
    : accessBranches(caller, [], {
        owned: true,
        shared: false,
        projectIds: [],
      });
  return { $and: [{ ...baseClause(caller, target), $or: branches }] };
}
