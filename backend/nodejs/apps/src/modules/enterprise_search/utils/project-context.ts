import { Types } from 'mongoose';
import { BadRequestError, ForbiddenError, NotFoundError } from '../../../libs/errors/http.errors';
import { IProjectDocument } from '../../projects/types/project.interfaces';
import { ProjectService } from '../../projects/services/project.service';

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

/** Reads a string-id array off an unknown `filters`/`tools` payload value. Anything else (missing, non-array, non-string entries) reads as "not provided". */
function readIdArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.filter((v): v is string => typeof v === 'string');
}

/**
 * Narrows one request-supplied list to a project's allowed set: an empty/
 * absent request list means "the caller didn't narrow this turn" and falls
 * back to the *whole* project set (the composer's default, fully-selected
 * state); a non-empty request list is intersected with the project set so
 * the caller can only deselect, never add beyond what the project allows.
 */
function narrowToProjectScope(requested: string[] | undefined, projectSet: string[]): string[] {
  if (!requested || requested.length === 0) return projectSet;
  const allowed = new Set(projectSet);
  return requested.filter((id) => allowed.has(id));
}

/**
 * Enforces a project's *explicit* scope on an outgoing AI payload, in
 * place — a project chat may only reach the connectors/toolsets/KBs/MCPs
 * the project itself lists, and only ever *narrows* what the per-turn
 * request already carried, never adds to it (see the class doc's
 * "Narrowing only" rule). Call this *after* `assignToolsToPayload` at each
 * call site so `aiPayload.tools` already reflects the request when this
 * runs — this function is the one that gets the final say on both
 * `filters` and `tools` for a project-scoped turn:
 *  - `projectInstructions` — set whenever the project has instructions
 *    (additive; never touches `filters`/agent identity — see prompt_builder.py).
 *  - `filters.apps`/`filters.kb` — `requested ∩ project.knowledgeScope`,
 *    falling back to the *whole* project set when the request carried none
 *    for that dimension. `filters.kb` additionally always includes the
 *    project's own hidden linked Collection id, once it exists.
 *  - `strictScope: true` — tells `get_accessible_virtual_record_ids`
 *    (Python) to return *no* records for an empty effective scope instead
 *    of falling back to "search everything the user can access" (see
 *    `ChatQuery.strictScope` in chatbot.py/agent.py).
 *  - `tools` — `requested ∩ project.tools`, same empty-means-whole-set
 *    fallback. Agent mode only; `chatbot.py`'s `ChatQuery` has no `tools`
 *    field so plain chat mode silently ignores it.
 *
 * No-ops when `project` is undefined, so call sites can call this
 * unconditionally after resolving (or not) a project link.
 */
export function applyProjectScope(
  aiPayload: Record<string, unknown>,
  project: IProjectDocument | undefined,
): void {
  if (!project) return;
  const context = ProjectService.buildContext(project);

  if (context.instructions) {
    aiPayload.projectInstructions = context.instructions;
  }

  const requestedFilters =
    aiPayload.filters && typeof aiPayload.filters === 'object'
      ? { ...(aiPayload.filters as Record<string, unknown>) }
      : {};
  delete requestedFilters.apps;
  delete requestedFilters.kb;

  const effectiveApps = narrowToProjectScope(
    readIdArray((aiPayload.filters as Record<string, unknown> | undefined)?.apps),
    context.knowledgeScope?.apps ?? [],
  );
  const effectiveKb = narrowToProjectScope(
    readIdArray((aiPayload.filters as Record<string, unknown> | undefined)?.kb),
    context.knowledgeScope?.kb ?? [],
  );
  const kbIds = new Set(effectiveKb);
  if (context.linkedKnowledgeBaseId) {
    kbIds.add(context.linkedKnowledgeBaseId);
  }

  aiPayload.filters = {
    ...requestedFilters,
    apps: effectiveApps,
    kb: Array.from(kbIds),
  };
  aiPayload.strictScope = true;

  aiPayload.tools = narrowToProjectScope(
    readIdArray(aiPayload.tools)?.map(bareToolFullName),
    Array.from(new Set((context.tools ?? []).map(bareToolFullName))),
  );
}

/**
 * The composer keys tools as `${instanceId}:${fullName}` for per-instance selection but
 * ships the bare `fullName`, which is also what Python matches on. Older projects persisted
 * the prefixed key; normalising both sides keeps them comparable.
 */
export function bareToolFullName(key: string): string {
  const colon = key.indexOf(':');
  return colon >= 0 ? key.slice(colon + 1) : key;
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
