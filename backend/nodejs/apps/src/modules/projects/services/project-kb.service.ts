import { NotFoundError, InternalServerError } from '../../../libs/errors/http.errors';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  executeConnectorCommand,
  handleBackendError,
} from '../../tokens_manager/utils/connector.utils';
import { AppConfig } from '../../tokens_manager/config/config';
import { getProjectRepository } from '../../../libs/repositories/project';
import { ProjectDto } from '../types/project.interfaces';

type KbRole = 'OWNER' | 'WRITER' | 'READER';

/** Maps a project member's role to the KB role granted on the linked hidden Collection — mirrors Collection sharing's own OWNER/WRITER/READER ladder. */
const KB_ROLE_BY_PROJECT_ROLE: Record<'editor' | 'viewer', KbRole> = {
  editor: 'WRITER',
  viewer: 'READER',
};

/** Deterministic id of the synthetic "every org member" team — see `ensure_all_team_with_users` (neo4j_provider.py ~6168). No lookup call needed; the id is a fixed `all_{orgId}` string. */
function allOrgTeamId(orgId: string): string {
  return `all_${orgId}`;
}

/** Current project members as the hidden-collection grant buckets. The project owner is never a writer/reader here — they are always KB OWNER. */
function memberGrantBuckets(project: ProjectDto): {
  editorUserIds: string[];
  viewerUserIds: string[];
  teamIds: string[];
} {
  const editorUserIds: string[] = [];
  const viewerUserIds: string[] = [];
  const teamIds: string[] = [];
  for (const member of project.members) {
    if (member.principalType === 'team') {
      teamIds.push(member.principalId);
      continue;
    }
    if (member.principalId === project.userId) {
      continue;
    }
    (member.role === 'editor' ? editorUserIds : viewerUserIds).push(member.principalId);
  }
  return { editorUserIds, viewerUserIds, teamIds };
}

/** Body for `POST /api/v1/kb/` so Python records the project owner as createdBy/OWNER and grants everyone else in the same create. */
function hiddenKbCreateBody(project: ProjectDto, projectId: string): Record<string, unknown> {
  const { editorUserIds, viewerUserIds, teamIds } = memberGrantBuckets(project);
  const teams =
    project.visibility === 'org' ? [...teamIds, allOrgTeamId(project.orgId)] : teamIds;
  const body: Record<string, unknown> = {
    name: `project:${projectId}`,
    isHidden: true,
    ownerUserId: project.userId,
  };
  if (editorUserIds.length > 0) {
    body.writerUserIds = editorUserIds;
  }
  if (viewerUserIds.length > 0) {
    body.readerUserIds = viewerUserIds;
  }
  if (teams.length > 0) {
    body.teamIds = teams;
  }
  return body;
}

/**
 * Owns the lifecycle and graph-permission sync of a project's hidden,
 * lazily-created linked Collection (`IProject.linkedKnowledgeBaseId`) —
 * the file store behind a project's Knowledge card, reusing the existing
 * Collections upload/index/search stack end to end. `ProjectService` stays
 * Mongo-only; this is the only place in the projects module that talks to
 * the Python `/api/v1/kb` HTTP surface.
 *
 * Every method that mutates graph permissions propagates failures instead
 * of swallowing them (a silently-failed revoke leaves a stale READER/WRITER
 * edge that this codebase has been bitten by before — see
 * `project.controller.ts`'s removed `syncFilePermissions` helper). Callers
 * that want best-effort behavior should catch explicitly at the call site.
 */
export class ProjectKnowledgeBaseService {
  private static async grantUsers(
    appConfig: AppConfig,
    headers: Record<string, string>,
    kbId: string,
    userIds: string[],
    role: KbRole,
  ): Promise<void> {
    if (userIds.length === 0) return;
    const response = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/kb/${encodeURIComponent(kbId)}/permissions`,
      HttpMethod.POST,
      headers,
      { userIds, teamIds: [], role },
    );
    if (response.statusCode !== 200 && response.statusCode !== 201) {
      throw handleBackendError(response, 'sync project knowledge base user permissions');
    }
  }

  private static async grantTeams(
    appConfig: AppConfig,
    headers: Record<string, string>,
    kbId: string,
    teamIds: string[],
  ): Promise<void> {
    if (teamIds.length === 0) return;
    const response = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/kb/${encodeURIComponent(kbId)}/permissions`,
      HttpMethod.POST,
      headers,
      // No `role` — the KB permission model has no concept of a team role
      // (`update_kb_permission` rejects one outright); every team
      // PERMISSION edge grants the same read access via
      // `_get_kb_virtual_ids`, regardless of the project's editor/viewer
      // distinction for that team. A known platform-level limitation, not
      // something this service can fix.
      { userIds: [], teamIds },
    );
    if (response.statusCode !== 200 && response.statusCode !== 201) {
      throw handleBackendError(response, 'sync project knowledge base team permissions');
    }
  }

  /**
   * Lazily creates the project's hidden linked Collection on first use and
   * returns its id — a no-op returning the existing id on every call after
   * the first. Race-safe: concurrent callers (e.g. two members uploading a
   * first file at once) each create a candidate KB, but only one wins the
   * `findOneAndUpdate` guarded on the *previously read* value of
   * `linkedKnowledgeBaseId`; the loser deletes its now-orphaned KB and
   * defers to the winner's id. Self-heals a 404 (Mongo points at a KB that
   * was hard-deleted upstream) by treating it as "not linked" and
   * recreating.
   */
  static async ensureLinkedKb(
    appConfig: AppConfig,
    headers: Record<string, string>,
    orgId: string,
    projectId: string,
  ): Promise<string> {
    const project = await getProjectRepository().getLive(projectId);
    // Tenant isolation defense-in-depth — callers normally reach this only
    // after `ProjectService.assertAccess`, but a caller-supplied `orgId`
    // must never be trusted to already match without a check here too.
    if (!project || project.orgId !== orgId) {
      throw new NotFoundError('Project not found');
    }

    const previousKbId = project.linkedKnowledgeBaseId ?? null;
    if (previousKbId) {
      const check = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${encodeURIComponent(previousKbId)}`,
        HttpMethod.GET,
        headers,
      );
      if (check.statusCode !== 404) {
        // Collections created before ownerUserId kept the first opener as
        // OWNER/createdBy. Re-sync when that opener (or the project owner)
        // still holds OWNER so the real owner is granted and the opener can
        // later be revoked. A WRITER opener is already the post-fix shape.
        const role = (check.data as { userRole?: string } | undefined)?.userRole;
        if (role === 'OWNER') {
          await this.syncMemberPermissions(appConfig, headers, project).catch(
            () => undefined,
          );
        }
        return previousKbId;
      }
      // Self-heal: Mongo points at a KB that no longer exists upstream —
      // fall through and recreate, racing on the same stale value below.
    }

    const createResponse = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/kb/`,
      HttpMethod.POST,
      headers,
      hiddenKbCreateBody(project, projectId),
    );
    if (createResponse.statusCode !== 200 && createResponse.statusCode !== 201) {
      throw handleBackendError(createResponse, 'create project knowledge base');
    }
    const kbId = (createResponse.data as { id?: string } | undefined)?.id;
    if (!kbId) {
      throw new InternalServerError('Knowledge base creation did not return an id');
    }

    const won = await getProjectRepository().claimLinkedKb(
      projectId,
      previousKbId,
      kbId,
    );

    if (!won) {
      // Lost the race — someone else linked a KB first. Delete the orphan
      // and defer to whatever the winner actually set.
      await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/kb/${encodeURIComponent(kbId)}`,
        HttpMethod.DELETE,
        headers,
      ).catch(() => undefined);
      const winner = await getProjectRepository().getLive(projectId);
      if (!winner?.linkedKnowledgeBaseId) {
        throw new InternalServerError('Failed to link project knowledge base');
      }
      return winner.linkedKnowledgeBaseId;
    }

    // Create already recorded the project owner as createdBy/OWNER and
    // granted current members. An editor's headers cannot share the KB
    // (only OWNER may), so do not sync here — that would 403 and leave a
    // linked collection the editor cannot finish setting up.
    return kbId;
  }

  /**
   * Grants (never revokes) the KB permissions implied by a project's
   * *current* member list — the project owner as KB `OWNER`, editor/viewer
   * users as `WRITER`/`READER`, every team member (regardless of role, see
   * `grantTeams`) as a role-less team PERMISSION, and the synthetic
   * `all_{orgId}` team as `READER` when `visibility === 'org'`. Idempotent:
   * the underlying graph write is a `MERGE`, so calling this repeatedly
   * with the same members is a safe no-op. Does not revoke a since-removed
   * member's stale edge — callers must pair a removal with
   * `revokePrincipalPermission`.
   */
  static async syncMemberPermissions(
    appConfig: AppConfig,
    headers: Record<string, string>,
    project: ProjectDto,
  ): Promise<void> {
    const kbId = project.linkedKnowledgeBaseId;
    if (!kbId) return;

    await this.grantUsers(appConfig, headers, kbId, [project.userId], 'OWNER');

    const { editorUserIds, viewerUserIds, teamIds } = memberGrantBuckets(project);

    await this.grantUsers(appConfig, headers, kbId, viewerUserIds, KB_ROLE_BY_PROJECT_ROLE.viewer);
    await this.grantTeams(appConfig, headers, kbId, teamIds);

    if (project.visibility === 'org') {
      await this.grantTeams(appConfig, headers, kbId, [allOrgTeamId(project.orgId)]);
    }

    // Editors last so a repair of an old editor-created KB can grant the
    // project owner and viewers first, then demote that leftover OWNER.
    await this.grantUsers(
      appConfig,
      headers,
      kbId,
      editorUserIds,
      KB_ROLE_BY_PROJECT_ROLE.editor,
    );
  }

  /** Revokes one principal's KB permission — pair with every `ProjectService.removeMember` call so a removed member doesn't keep a stale READER/WRITER edge. No-op if the project has no linked KB yet, or if the principal never had a KB-level permission (upstream 404). */
  static async revokePrincipalPermission(
    appConfig: AppConfig,
    headers: Record<string, string>,
    project: ProjectDto,
    principalId: string,
    principalType: 'user' | 'team',
  ): Promise<void> {
    const kbId = project.linkedKnowledgeBaseId;
    if (!kbId) return;
    const body =
      principalType === 'team'
        ? { userIds: [], teamIds: [principalId] }
        : { userIds: [principalId], teamIds: [] };
    const response = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/kb/${encodeURIComponent(kbId)}/permissions`,
      HttpMethod.DELETE,
      headers,
      body,
    );
    if (response.statusCode !== 200 && response.statusCode !== 404) {
      throw handleBackendError(response, 'revoke project knowledge base permission');
    }
  }

  /** Revokes the `all_{orgId}` team's READER edge — call when a project's `visibility` changes away from `'org'`. No-op without a linked KB, or if the edge was never granted (upstream 404). */
  static async revokeOrgVisibility(
    appConfig: AppConfig,
    headers: Record<string, string>,
    project: ProjectDto,
  ): Promise<void> {
    const kbId = project.linkedKnowledgeBaseId;
    if (!kbId) return;
    const response = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/kb/${encodeURIComponent(kbId)}/permissions`,
      HttpMethod.DELETE,
      headers,
      { userIds: [], teamIds: [allOrgTeamId(project.orgId)] },
    );
    if (response.statusCode !== 200 && response.statusCode !== 404) {
      throw handleBackendError(response, 'revoke project knowledge base org visibility');
    }
  }

  /** Deletes the project's linked KB (cascades its records/vectors upstream) — idempotent; a 404 (already gone) is treated as success so a retried `softDelete` never fails on this step. No-op without a linked KB. */
  static async deleteLinkedKb(
    appConfig: AppConfig,
    headers: Record<string, string>,
    project: ProjectDto,
  ): Promise<void> {
    const kbId = project.linkedKnowledgeBaseId;
    if (!kbId) return;
    const response = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/kb/${encodeURIComponent(kbId)}`,
      HttpMethod.DELETE,
      headers,
    );
    if (response.statusCode !== 200 && response.statusCode !== 404) {
      throw handleBackendError(response, 'delete project knowledge base');
    }
  }
}

export default ProjectKnowledgeBaseService;
