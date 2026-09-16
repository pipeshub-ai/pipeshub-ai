import { Types } from 'mongoose';
import { BadRequestError, ForbiddenError, NotFoundError } from '../../../libs/errors/http.errors';
import { IProjectDocument } from '../../projects/types/project.interfaces';
import { ProjectService } from '../../projects/services/project.service';
import { IChatAttachmentRef } from '../types/conversation.interfaces';

/** Sentinel accepted by `?projectId=` query params to mean "no project". */
export const PROJECT_ID_UNASSIGNED = 'unassigned';

export interface ResolvedProjectLink {
  projectId?: string;
  projectVisibility?: 'private' | 'project';
  project?: IProjectDocument;
}

/**
 * Validates an inbound `projectId` (if present in the request body) against
 * the caller's access and resolves the `projectVisibility` to persist on a
 * *new* chat/agent session. An explicit `projectVisibility` in the body
 * wins; otherwise it defaults from the project's `chatSharing` setting
 * ('members' -> 'project', anything else -> 'private' — see plan's "Chats
 * in shared projects are private by default" decision).
 *
 * Returns `{}` (no-op) when the caller didn't supply a projectId — the
 * common, non-project chat path is untouched.
 */
export async function resolveProjectLink(
  orgId: string,
  userId: string,
  body: Record<string, unknown>,
): Promise<ResolvedProjectLink> {
  const projectId =
    typeof body.projectId === 'string' ? body.projectId : undefined;
  if (!projectId) {
    return {};
  }
  const { project } = await ProjectService.assertAccess(
    orgId,
    userId,
    projectId,
    'viewer',
  );
  const requestedVisibility = body.projectVisibility;
  const projectVisibility: 'private' | 'project' =
    requestedVisibility === 'project' || requestedVisibility === 'private'
      ? requestedVisibility
      : project.chatSharing === 'members'
        ? 'project'
        : 'private';
  return { projectId, projectVisibility, project };
}

/** True when a `filters` object (either shape) carries at least one app/kb id. */
function hasNonEmptyFilters(filters: unknown): boolean {
  if (!filters || typeof filters !== 'object') return false;
  const { apps, kb } = filters as { apps?: unknown[]; kb?: unknown[] };
  return (
    (Array.isArray(apps) && apps.length > 0) ||
    (Array.isArray(kb) && kb.length > 0)
  );
}

/**
 * Merges a project's instructions / knowledge scope / files into an
 * outgoing AI payload, in place. Request-supplied `filters` and
 * `attachments` always take precedence — the project only fills gaps the
 * caller left empty:
 *  - `projectInstructions` is set whenever the project has instructions
 *    (additive; never touches `filters`/agent identity — see prompt_builder.py).
 *  - `filters` falls back to the project's `knowledgeScope` only when the
 *    request itself carried no apps/kb filter.
 *  - `attachments` are the union of request attachments and project files,
 *    de-duplicated by `recordId` (request wins on conflict).
 *
 * No-ops when `project` is undefined, so call sites can call this
 * unconditionally after resolving (or not) a project link.
 */
export function applyProjectContext(
  aiPayload: Record<string, unknown>,
  project: IProjectDocument | undefined,
): void {
  if (!project) return;
  const context = ProjectService.buildContext(project);

  if (context.instructions) {
    aiPayload.projectInstructions = context.instructions;
  }

  if (!hasNonEmptyFilters(aiPayload.filters) && context.knowledgeScope) {
    aiPayload.filters = context.knowledgeScope;
  }

  const projectAttachments = context.attachments ?? [];
  if (projectAttachments.length > 0) {
    const existing =
      (aiPayload.attachments as IChatAttachmentRef[] | undefined) ?? [];
    const seen = new Set(existing.map((a) => a.recordId));
    const merged = [...existing];
    for (const attachment of projectAttachments) {
      if (!seen.has(attachment.recordId)) {
        merged.push(attachment);
        seen.add(attachment.recordId);
      }
    }
    aiPayload.attachments = merged;
  }
}

/**
 * Loads the project for an *existing* session's `projectId` (follow-up
 * turns never trust a client-supplied projectId — see plan's "projectId on
 * follow-up requests is ignored; the session row is the source of truth").
 * Returns undefined for a plain (non-project) session or if the linked
 * project was hard-deleted from under it — either way the chat should keep
 * working without project context rather than failing the turn.
 */
export async function loadProjectForSession(
  orgId: string,
  userId: string,
  projectId: Types.ObjectId | string | undefined,
): Promise<IProjectDocument | undefined> {
  if (!projectId) return undefined;
  try {
    const { project } = await ProjectService.assertAccess(
      orgId,
      userId,
      projectId.toString(),
      'viewer',
    );
    return project;
  } catch (error) {
    if (error instanceof NotFoundError || error instanceof ForbiddenError || error instanceof BadRequestError) {
      return undefined;
    }
    throw error;
  }
}
