import { Types } from 'mongoose';
import { projectFactsOf } from '../../../../authz/loaders/project.loader';
import { ProjectFacts } from '../../../../authz/domain/types';
import { Project } from '../../../../projects/schema/project.schema';
import {
  resolveRole,
  toAccessView,
} from '../access/conversation-access.policy';
import { archiveClause } from '../access/conversation-access.filters';
import { AccessView, ConversationAccessFields } from '../domain/types';
import { ConversationRequestContext } from './conversation-context';

type ListRow = ConversationAccessFields & { _id: { toString(): string } };

/** Access views for list rows keyed by row id; empty with the flag off. One project read per page covers every project-inherited row. */
export async function listAccessViews(
  ctx: ConversationRequestContext,
  rows: readonly ListRow[],
): Promise<Map<string, AccessView>> {
  const views = new Map<string, AccessView>();
  if (ctx.collab !== true || rows.length === 0) {
    return views;
  }
  const { caller } = ctx;
  const projectIds = [
    ...new Set(
      rows.flatMap((row) =>
        row.projectVisibility === 'project' && row.projectId
          ? [row.projectId.toString()]
          : [],
      ),
    ),
  ];
  const projects = new Map<string, ProjectFacts>();
  if (projectIds.length > 0) {
    const found = await Project.find({
      _id: { $in: projectIds.map((id) => new Types.ObjectId(id)) },
      orgId: new Types.ObjectId(caller.orgId),
      isDeleted: false,
    }).lean();
    for (const project of found) {
      projects.set(project._id.toString(), projectFactsOf(project));
    }
  }
  for (const row of rows) {
    const resolved = resolveRole(row, caller, {
      collab: true,
      project: projects.get(row.projectId?.toString() ?? '') ?? null,
    });
    if ('unresolved' in resolved || resolved.role === 'none') {
      continue;
    }
    views.set(
      row._id.toString(),
      toAccessView(resolved.role, row, caller, { collab: true }),
    );
  }
  return views;
}

/** I-9: with the flag on, only a chat's owner sees who else it is shared with; the flag-off row shape is unchanged. */
export function redactRecipients<
  T extends { userId?: { toString(): string }; sharedWith?: unknown },
>(ctx: ConversationRequestContext, row: T): T {
  if (ctx.collab !== true || row.userId?.toString() === ctx.caller.userId) {
    return row;
  }
  return { ...row, sharedWith: undefined };
}

/** State of an archive list. With the flag on, membership is the list scope's `archived: 'only'` clause, since a per-user archive leaves `isArchived` false. */
export function archivedStateFilter(
  ctx: ConversationRequestContext,
): Record<string, unknown> {
  return ctx.collab === true
    ? {}
    : { isArchived: true, archivedBy: { $exists: true } };
}

/** The same clause for archive lists that build their own owner-only filter instead of using the list scope. */
export function ownArchivedClause(
  ctx: ConversationRequestContext,
): Record<string, unknown> | undefined {
  return ctx.collab === true ? archiveClause(ctx.caller, 'only') : undefined;
}
