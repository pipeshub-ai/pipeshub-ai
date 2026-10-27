import { Types } from 'mongoose';
import { BadRequestError, ForbiddenError, NotFoundError } from '../../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { IProjectDocument } from '../../projects/types/project.interfaces';
import { ProjectService } from '../../projects/services/project.service';
import { Project } from '../../projects/schema/project.schema';
import { resolveCallerTeamIds } from '../../projects/utils/team-membership';
import { ProjectsFeature } from '../../projects/utils/projects-feature';
import { ChatSession } from '../schema/chat.session.schema';

const logger = Logger.getInstance({ service: 'ProjectContext' });

/** Sentinel accepted by `?projectId=` query params to mean "no project". */
export const PROJECT_ID_UNASSIGNED = 'unassigned';

export interface ResolvedProjectLink {
  projectId?: string;
  projectVisibility?: 'private' | 'project';
  project?: IProjectDocument;
}

/** Who is asking, for the project access checks a chat makes. */
export interface ProjectCaller {
  /** Graph team ids, fetched only when a chat actually involves a project. */
  resolveTeamIds: () => Promise<string[]>;
  /** The scopes of an OAuth token or PAT; undefined for a session token, which is not scope-limited. */
  oauthScopes?: string[];
}

type TokenFields = { isOAuth?: boolean; oauthScopes?: string[] };

export function projectCallerFromRequest(
  req: AuthenticatedUserRequest,
  appConfig: AppConfig,
): ProjectCaller {
  const user = req.user as TokenFields | undefined;
  return {
    resolveTeamIds: () => resolveCallerTeamIds(req, appConfig),
    oauthScopes: user?.isOAuth === true ? (user.oauthScopes ?? []) : undefined,
  };
}

/**
 * Projects whose shared chats the caller may read. Teams are resolved only
 * when the org has a project shared with a team, so the common case adds no
 * call to the connector service on every chat list.
 */
export async function accessibleProjectIdsFor(
  orgId: string,
  userId: string,
  caller?: ProjectCaller,
): Promise<Types.ObjectId[]> {
  const teamIds =
    caller && (await ProjectService.orgHasTeamMembers(orgId))
      ? await caller.resolveTeamIds()
      : [];
  return ProjectService.getAccessibleProjectIds(orgId, userId, teamIds);
}

/**
 * A chat in a project reads the project's instructions, scope and files, so
 * an OAuth token or PAT needs project:read for it, as for GET /projects/:id.
 * conversation:chat alone used to reach all of it.
 */
function assertProjectReadScope(caller: ProjectCaller | undefined): void {
  const scopes = caller?.oauthScopes;
  if (scopes && !scopes.includes(OAuthScopeNames.PROJECT_READ)) {
    throw new ForbiddenError(
      `Insufficient scope. Required: ${OAuthScopeNames.PROJECT_READ}`,
    );
  }
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
  caller?: ProjectCaller,
): Promise<ResolvedProjectLink> {
  const projectId =
    typeof body.projectId === 'string' ? body.projectId : undefined;
  if (!projectId) {
    return {};
  }
  await ProjectsFeature.assertEnabled();
  assertProjectReadScope(caller);
  const { project } = await ProjectService.assertAccess(
    orgId,
    userId,
    projectId,
    'viewer',
    caller ? await caller.resolveTeamIds() : [],
  );
  ProjectService.assertNotArchived(project);
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

/** Filter keys that select below app level (record groups, folders, records). */
const SELECTION_FILTER_KEYS = ['recordGroups', 'records', 'recordsExact'];

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
 *  - `filters.recordGroups`/`filters.records` — what the request selected
 *    below app level, or, for a request that selected nothing at all, what
 *    the project itself lists there. Either way `allowedFilters` carries the
 *    project's whole scope, because only Python can tell whether a selected
 *    node lies inside it.
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

  const projectApps = context.knowledgeScope?.apps ?? [];
  const projectKb = context.knowledgeScope?.kb ?? [];
  const projectBelowApps = {
    recordGroups: context.knowledgeScope?.recordGroups ?? [],
    records: context.knowledgeScope?.records ?? [],
  };
  // A selection below app level is the whole scope of the turn: an empty
  // apps/kb list then means none of them, not the whole project.
  const hasSelection = SELECTION_FILTER_KEYS.some(
    (key) => (readIdArray(requestedFilters[key])?.length ?? 0) > 0,
  );
  const narrow = (
    requested: string[] | undefined,
    projectSet: string[],
  ): string[] =>
    hasSelection
      ? (requested ?? []).filter((id) => projectSet.includes(id))
      : narrowToProjectScope(requested, projectSet);
  const requested = aiPayload.filters as Record<string, unknown> | undefined;
  const requestedApps = readIdArray(requested?.apps);
  const requestedKb = readIdArray(requested?.kb);
  // A collection may be sent under `apps` like any other app.
  const collectionsUnderApps = (requestedApps ?? []).filter((id) =>
    projectKb.includes(id),
  );
  const effectiveApps = narrow(requestedApps, projectApps);
  const effectiveKb = narrow(
    collectionsUnderApps.length > 0
      ? [...(requestedKb ?? []), ...collectionsUnderApps]
      : requestedKb,
    projectKb,
  );
  const kbIds = new Set(effectiveKb);
  if (context.linkedKnowledgeBaseId) {
    kbIds.add(context.linkedKnowledgeBaseId);
  }
  // A request that selected nothing is the whole project, including what the
  // project lists below app level.
  const requestedNothing =
    !hasSelection && !requestedApps?.length && !requestedKb?.length;
  const belowApps = requestedNothing
    ? Object.fromEntries(
        Object.entries(projectBelowApps).filter(([, ids]) => ids.length > 0),
      )
    : {};
  if (hasSelection || Object.keys(belowApps).length > 0) {
    aiPayload.allowedFilters = {
      apps: [
        ...projectApps,
        ...projectKb,
        ...(context.linkedKnowledgeBaseId
          ? [context.linkedKnowledgeBaseId]
          : []),
      ],
      ...projectBelowApps,
    };
  }

  aiPayload.filters = {
    ...requestedFilters,
    ...belowApps,
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
 *
 * When the chat's owner has lost access to its project (removed as a
 * member, or the project made private), the chat is unlinked from the
 * project before the turn runs. Otherwise their new messages kept landing in
 * a chat the project's members can still read.
 */
export async function loadProjectForSession(
  orgId: string,
  userId: string,
  projectId: Types.ObjectId | string | undefined,
  options: {
    caller?: ProjectCaller;
    conversationId?: Types.ObjectId | string;
  } = {},
): Promise<IProjectDocument | undefined> {
  if (!projectId) return undefined;
  // With Projects off the chat carries on without project context; the link
  // is kept, so turning Projects back on restores it.
  if (!(await ProjectsFeature.isEnabled())) return undefined;
  assertProjectReadScope(options.caller);
  try {
    const { project } = await ProjectService.assertAccess(
      orgId,
      userId,
      projectId.toString(),
      'viewer',
      options.caller ? await options.caller.resolveTeamIds() : [],
    );
    return project;
  } catch (error) {
    if (error instanceof NotFoundError || error instanceof ForbiddenError || error instanceof BadRequestError) {
      if (
        options.conversationId &&
        Types.ObjectId.isValid(projectId.toString())
      ) {
        await unlinkFromProject(orgId, options.conversationId, projectId);
      }
      return undefined;
    }
    throw error;
  }
}

async function unlinkFromProject(
  orgId: string,
  conversationId: Types.ObjectId | string,
  projectId: Types.ObjectId | string,
): Promise<void> {
  // A failed team lookup reads as "in no teams", which would wrongly unlink a
  // chat whose owner has access through a team, so such projects are left alone.
  // A deleted project already had its chats unlinked by its delete.
  const stillRestricted = await Project.exists({
    _id: new Types.ObjectId(projectId.toString()),
    isDeleted: false,
    'members.principalType': { $ne: 'team' },
  });
  if (!stillRestricted) return;
  // Guarded on the same projectId so a chat moved elsewhere meanwhile stays put.
  await ChatSession.updateOne(
    {
      _id: new Types.ObjectId(conversationId.toString()),
      orgId: new Types.ObjectId(orgId),
      projectId: new Types.ObjectId(projectId.toString()),
    },
    { $unset: { projectId: '', projectVisibility: '' } },
  );
  logger.info('Unlinked a chat from a project its owner can no longer access', {
    conversationId: conversationId.toString(),
    projectId: projectId.toString(),
  });
}
